"""
API RoboSafe Sentinel.

Serveur REST + WebSocket pour supervision temps réel.

Endpoints:
    GET  /health           - Health check
    GET  /api/v1/status    - État global
    GET  /api/v1/signals   - Signaux temps réel
    GET  /api/v1/alerts    - Alertes actives
    POST /api/v1/command   - Commandes
    WS   /ws               - WebSocket temps réel
    GET  /metrics          - Métriques Prometheus
"""

from robosafe.api.server import app, init_api, run
from robosafe.api.websocket_manager import WebSocketManager
from robosafe.api.metrics import MetricsCollector

__all__ = [
    "app",
    "init_api",
    "run",
    "WebSocketManager",
    "MetricsCollector",
]
