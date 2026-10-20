
"""Shared monitoring orchestration, independent of the HTTP application."""

import asyncio
import logging
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from config import config
from database import db
from models import SearchQuery
from browser_engine import browser_engine
from http_engine import http_engine
from coordination import lease
from outbox import outbox_worker
from observability import observed, configure_logging
from region_filter import is_chechnya_item

configure_logging()
logger = logging.getLogger("AvitoMonitoring")


class MonitoringService:
    def __init__(self):
        self._run_lock = asyncio.Lock()
        self._search_locks = {}
        self.is_running: bool = False
        self.task: Optional[asyncio.Task] = None
        self.last_run: Optional[datetime] = None
        self.last_duration_seconds: Optional[float] = None
        self.next_run: Optional[datetime] = None
        self.current_checking: Optional[str] = None
        self.last_results: Dict[str, Any] = {
            "new": 0,
            "drops": 0,
            "errors": 0,
        }
        self.access_alerts: Dict[int, Dict[str, Any]] = {}

    def clear_access_alert(self, url):
        for search_id, alert in list(self.access_alerts.items()):
            if alert.get("url") == url:
                self.access_alerts.pop(search_id, None)

    def _access_failure(self, search):
        previous = self.access_alerts.get(search.id, {})
        count = previous.get("count", 0) + 1
        pause = (300, 900, 3600)[min(count - 1, 2)]

        self.access_alerts[search.id] = {
            "search_id": search.id,
            "name": search.name,
            "url": search.url,
            "count": count,
            "retry_at": time.time() + pause,
            "message": (
                f"Авито ограничил доступ ({count}). "
                f"Поиск на паузе {pause // 60} мин. "
                "Проверьте доступность сайта и настройки подключения."
            ),
        }

    async def start(self):
        if self.is_running:
            return

        self.is_running = True
        self.task = asyncio.create_task(self._loop())
        logger.info("Фоновый сервис мониторинга Авито запущен")

    async def stop(self):
        if not self.is_running:
            return

        self.is_running = False

        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None

        self.current_checking = None
        logger.info("Фоновый сервис мониторинга Авито остановлен")

    @observed("search")
    async def check_search(self, search):
        started_at = time.time()
        started_clock = time.monotonic()
        result = {
            "new": 0,
            "drops": 0,
            "skipped": 1,
            "outcome": "skipped",
        }
        error_type = None
        lock = self._search_locks.setdefault(
            search.id, asyncio.Lock()
        )

        try:
            alert = self.access_alerts.get(search.id)

            if alert and alert["retry_at"] > time.time():
                return {
                    "new": 0,
                    "drops": 0,
                    "skipped": 1,
                    "outcome": "skipped",
                    "error_type": "AccessCooldown",
                }

            if lock.locked():
                return result

            async with lock:
                async with lease(f"search:{search.id}") as acquired:
                    if acquired:
                        result = await asyncio.wait_for(
                            self._check_search(search),
                            timeout=180,
                        )

                        if result.get("outcome") == "blocked":
                            self._access_failure(search)
                        elif result.get("outcome") in ("success", "empty"):
                            self.clear_access_alert(search.url)

            return result

        except asyncio.CancelledError:
            error_type = "CancelledError"
            result = {
                "new": 0,
                "drops": 0,
                "errors": 1,
                "outcome": "error",
            }
            raise

        except Exception as exc:
            error_type = type(exc).__name__
            result = {
                "new": 0,
                "drops": 0,
                "errors": 1,
                "outcome": "error",
            }
            logger.exception(
                "Проверка поиска #%s завершилась ошибкой",
                search.id,
            )
            return result

        finally:
            record = {
                "run_id": uuid.uuid4().hex,
                "search_id": search.id,
                "search_name": search.name,
                "started_at": started_at,
                "finished_at": time.time(),
                "duration_seconds": max(
                    0, time.monotonic() - started_clock
                ),
                "outcome": result.get("outcome", "error"),
                "engine": result.get("engine"),
                "found_count": result.get("found", 0),
                "new_count": result.get("new", 0),
                "drops_count": result.get("drops", 0),
                "error_type": error_type or result.get("error_type"),
            }

            try:
                await db.record_monitoring_run(record)
            except Exception:
                logger.exception(
                    "Не удалось сохранить историю поиска #%s",
                    search.id,
                )

    async def _check_search(self, search) -> Dict[str, Any]:
        """Проверяет поиск, фильтрует объявления и сохраняет результаты."""

        now_hour = datetime.now().hour
        start = getattr(search, "active_hours_start", 0)
        end = getattr(search, "active_hours_end", 24)

        if start < end:
            if now_hour < start or now_hour >= end:
                logger.info(
                    "Поиск '%s' вне активных часов (%s:00-%s:00)",
                    search.name, start, end,
                )
                return {
                    "new": 0, "drops": 0,
                    "skipped": 1, "outcome": "skipped",
                }
        else:
            if end <= now_hour < start:
                logger.info(
                    "Поиск '%s' вне активных часов (%s:00-%s:00)",
                    search.name, start, end,
                )
                return {
                    "new": 0, "drops": 0,
                    "skipped": 1, "outcome": "skipped",
                }

        self.current_checking = search.name
        logger.info(
            "Проверка поиска #%s '%s'",
            search.id, search.name,
        )

        new_count = 0
        drop_count = 0
        result = None
        engine_used = "http"

        try:
            # Сначала пробуем HTTP-парсер.
            try:
                result = await http_engine.parse_search(
                    search.url, max_pages=1
                )
            except Exception:
                logger.exception(
                    "Ошибка HTTP-парсера для '%s'",
                    search.name,
                )
                result = None

            # Если HTTP не дал пригодного результата — пробуем браузер.
            if result and result.outcome == "blocked":
                return {
                    "new": 0, "drops": 0, "errors": 1,
                    "outcome": "blocked", "engine": "http",
                }

            if result is None or (
                not result.items and result.outcome != "empty"
            ):
                engine_used = "browser"
                result = await browser_engine.parse_search(
                    search.url, max_pages=1
                )

            logger.info(
                "Для поиска '%s' используется движок: %s",
                search.name, engine_used,
            )

            if result.outcome in ("blocked", "error"):
                return {
                    "new": 0,
                    "drops": 0,
                    "errors": 1,
                    "outcome": result.outcome,
                    "engine": engine_used,
                }

            # Фильтруем объявления по адресу в Чеченской Республике.
            original_count = len(result.items or [])
            result.items = [
                item
                for item in (result.items or [])
                if is_chechnya_item(item)
            ]

            logger.info(
                "Фильтр Чечни: оставлено %s из %s объявлений",
                len(result.items),
                original_count,
            )

            # Сохраняем только объявления, прошедшие фильтр.
            if result.items:
                for item in result.items:
                    item.search_query_id = search.id

                save_result = await db.save_items(result.items)
                new_count = save_result["new_count"]
                drop_count = save_result["price_drop_count"]

                # Не закрываем старые объявления, если результат
                # неполный или парсер сообщил об ошибках.
                if result.exhaustive and not result.errors:
                    active_ids = [item.id for item in result.items]
                    closed_count = await db.cleanup_stale_items(
                        search.id, active_ids
                    )

                    if closed_count:
                        logger.info(
                            "Поиск '%s': скрыто объявлений: %s",
                            search.name, closed_count,
                        )

                try:
                    stats = await db.get_gem_lifetime_stats()
                    if stats and stats.get("count", 0) > 0:
                        logger.info(
                            "Среднее время жизни выгодных лотов: %.1f ч "
                            "(выборка: %s)",
                            stats.get("avg_hours", 0),
                            stats["count"],
                        )
                except Exception:
                    logger.exception(
                        "Не удалось получить статистику лотов"
                    )

                await outbox_worker.drain()

            outcome = result.outcome
            if not result.items and outcome == "success":
                outcome = "empty"

            return {
                "new": new_count,
                "drops": drop_count,
                "found": len(result.items),
                "outcome": outcome,
                "engine": engine_used,
            }

        except Exception as exc:
            logger.exception(
                "Ошибка при проверке поиска #%s",
                search.id,
            )
            return {
                "new": new_count,
                "drops": drop_count,
                "errors": 1,
                "outcome": "error",
                "error_type": type(exc).__name__,
                "engine": engine_used,
            }

        finally:
            self.current_checking = None
            if search.id is not None:
                try:
                    await db.update_search_last_checked(search.id)
                except Exception:
                    logger.exception(
                        "Не удалось обновить время проверки #%s",
                        search.id,
                    )

    @observed("monitoring-cycle")
    async def run_all_now(self, due_only=False):
        if self._run_lock.locked():
            return {
                "new": 0, "drops": 0,
                "errors": 0, "skipped": 1,
            }

        async with self._run_lock:
            async with lease("monitoring-cycle") as acquired:
                if not acquired:
                    return {
                        "new": 0, "drops": 0,
                        "errors": 0, "skipped": 1,
                    }

                return await self._run_all_now(due_only=due_only)

    async def _run_all_now(self, due_only=False) -> Dict[str, int]:
        """Проверяет активные поиски, максимум три одновременно."""

        searches = await db.get_searches(enabled_only=True)
        searches = [
            search for search in searches
            if self.access_alerts.get(
                search.id, {}
            ).get("retry_at", 0) <= time.time()
        ]

        if due_only:
            now = datetime.now()
            searches = [
                search for search in searches
                if search.last_checked_at is None
                or now.timestamp()
                - search.last_checked_at.timestamp()
                >= 60 * max(1, search.check_interval_min)
            ]

        total_new = 0
        total_drops = 0
        self.last_run = datetime.now()
        started_clock = time.monotonic()
        semaphore = asyncio.Semaphore(3)

        async def check_with_semaphore(search):
            async with semaphore:
                return await self.check_search(search)

        tasks = [
            check_with_semaphore(search)
            for search in searches
        ]
        results = await asyncio.gather(
            *tasks, return_exceptions=True
        )

        errors = 0
        for item in results:
            if isinstance(item, Exception):
                errors += 1
                continue

            total_new += item.get("new", 0)
            total_drops += item.get("drops", 0)
            errors += item.get("errors", 0)

        self.last_duration_seconds = time.monotonic() - started_clock
        self.last_results = {
            "new": total_new,
            "drops": total_drops,
            "errors": errors,
        }

        self.next_run = datetime.now() + timedelta(seconds=30)
        return self.last_results

    async def _loop(self):
        while self.is_running:
            try:
                await self.run_all_now(due_only=True)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Ошибка в цикле мониторинга")

            self.next_run = datetime.now() + timedelta(seconds=30)

            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                break


monitor_service = MonitoringService()
