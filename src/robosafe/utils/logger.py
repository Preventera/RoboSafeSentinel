"""
Configuration du logging structuré RoboSafe.

Utilise structlog pour un logging JSON adapté à l'observabilité.
"""

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import structlog
from structlog.types import Processor


def add_timestamp(
    logger: logging.Logger,
    method_name: str,
    event_dict: dict,
) -> dict:
    """Ajoute un timestamp ISO."""
    event_dict["timestamp"] = datetime.now().isoformat()
    return event_dict


def add_service_info(
    logger: logging.Logger,
    method_name: str,
    event_dict: dict,
) -> dict:
    """Ajoute les infos du service."""
    event_dict["service"] = "robosafe-sentinel"
    return event_dict


def setup_logging(
    level: str = "INFO",
    format: str = "json",
    log_file: Optional[Path] = None,
) -> None:
    """
    Configure le logging structuré.
    
    Args:
        level: Niveau de log (DEBUG, INFO, WARNING, ERROR)
        format: Format de sortie (json, console)
        log_file: Fichier de sortie optionnel
    """
    # Processors communs
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        add_timestamp,
        add_service_info,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    
    if format == "json":
        # Format JSON pour production
        processors = shared_processors + [
            structlog.processors.JSONRenderer()
        ]
    else:
        # Format console pour développement
        processors = shared_processors + [
            structlog.dev.ConsoleRenderer(colors=True)
        ]
    
    # Configurer structlog
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper())
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    
    # Configurer logging standard aussi (pour libs externes)
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper()),
    )
    
    # Fichier si demandé
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(log_file, encoding="utf-8")
        handler.setLevel(getattr(logging, level.upper()))
        logging.getLogger().addHandler(handler)
    
    structlog.get_logger().info(
        "logging_configured",
        level=level,
        format=format,
        log_file=str(log_file) if log_file else None,
    )
