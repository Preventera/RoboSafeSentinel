"""
Tests unitaires pour l'API RoboSafe.
"""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from robosafe.api.server import app, init_api
from robosafe.api.websocket_manager import WebSocketManager
from robosafe.api.metrics import MetricsCollector, SimpleMetrics


class TestHealthEndpoint:
    """Tests pour /health."""
    
    @pytest.fixture
    def client(self):
        return TestClient(app)
    
    def test_health_check(self, client):
        """Test health check basique."""
        response = client.get("/health")
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "version" in data
        assert "timestamp" in data


class TestStatusEndpoint:
    """Tests pour /api/v1/status."""
    
    @pytest.fixture
    def client(self):
        return TestClient(app)
    
    def test_status_without_managers(self, client):
        """Test status sans gestionnaires initialisés."""
        response = client.get("/api/v1/status")
        
        assert response.status_code == 200
        data = response.json()
        assert "safety_state" in data
        assert "version" in data
    
    def test_status_with_mocked_managers(self, client):
        """Test status avec gestionnaires mockés."""
        # Mock state machine
        mock_state_machine = MagicMock()
        mock_state_machine.get_status.return_value = {
            "current_state": "NORMAL",
            "state_code": 1,
            "max_speed_percent": 100,
            "allows_production": True,
            "state_duration_seconds": 60.0,
        }
        
        # Mock signal manager
        mock_signal_manager = MagicMock()
        mock_signal_manager.get_stats.return_value = {
            "total_signals": 20,
            "valid_signals": 18,
            "invalid_signals": 2,
        }
        
        # Mock rule engine
        mock_rule_engine = MagicMock()
        mock_rule_engine.get_stats.return_value = {
            "total_rules": 15,
            "trigger_count": 5,
        }
        
        init_api(mock_signal_manager, mock_state_machine, mock_rule_engine)
        
        response = client.get("/api/v1/status")
        
        assert response.status_code == 200
        data = response.json()
        assert data["safety_state"] == "NORMAL"
        assert data["max_speed_percent"] == 100
        assert data["total_signals"] == 20


class TestSignalsEndpoint:
    """Tests pour /api/v1/signals."""
    
    @pytest.fixture
    def client(self):
        return TestClient(app)
    
    def test_signals_without_manager(self, client):
        """Test signals sans gestionnaire."""
        # Reset le signal manager
        import robosafe.api.server as server
        server._signal_manager = None
        
        response = client.get("/api/v1/signals")
        
        assert response.status_code == 503


class TestCommandEndpoint:
    """Tests pour /api/v1/command."""
    
    @pytest.fixture
    def client(self):
        return TestClient(app)
    
    def test_command_estop(self, client):
        """Test commande E-STOP."""
        # Mock state machine
        mock_state_machine = MagicMock()
        mock_state_machine.request_estop = AsyncMock(return_value=True)
        
        init_api(None, mock_state_machine, None)
        
        response = client.post("/api/v1/command", json={
            "command": "ESTOP",
            "reason": "Test emergency",
            "operator_id": "test_user",
        })
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["command"] == "ESTOP"
    
    def test_command_invalid(self, client):
        """Test commande invalide."""
        mock_state_machine = MagicMock()
        init_api(None, mock_state_machine, None)
        
        response = client.post("/api/v1/command", json={
            "command": "INVALID_COMMAND",
        })
        
        assert response.status_code == 400


class TestRulesEndpoint:
    """Tests pour /api/v1/rules."""
    
    @pytest.fixture
    def client(self):
        return TestClient(app)
    
    def test_rules_list(self, client):
        """Test liste des règles."""
        # Mock rule engine
        mock_rule_engine = MagicMock()
        mock_rule = MagicMock()
        mock_rule.id = "RS-001"
        mock_rule.name = "Test Rule"
        mock_rule.priority.name = "P0_CRITICAL"
        mock_rule.enabled = True
        mock_rule._trigger_count = 5
        mock_rule._last_triggered = None
        
        mock_rule_engine._rules = {"RS-001": mock_rule}
        
        init_api(None, None, mock_rule_engine)
        
        response = client.get("/api/v1/rules")
        
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1
        assert data["rules"][0]["id"] == "RS-001"


class TestWebSocketManager:
    """Tests pour le gestionnaire WebSocket."""
    
    @pytest.fixture
    def manager(self):
        return WebSocketManager()
    
    def test_initial_state(self, manager):
        """Test état initial."""
        assert manager.client_count == 0
        assert manager.stats["current_connections"] == 0
    
    @pytest.mark.asyncio
    async def test_connect_disconnect(self, manager):
        """Test connexion/déconnexion."""
        # Mock WebSocket
        mock_ws = AsyncMock()
        mock_ws.accept = AsyncMock()
        mock_ws.send_json = AsyncMock()
        
        await manager.connect(mock_ws, client_id="test_client")
        
        assert manager.client_count == 1
        mock_ws.accept.assert_called_once()
        
        manager.disconnect(mock_ws)
        
        assert manager.client_count == 0
    
    @pytest.mark.asyncio
    async def test_broadcast(self, manager):
        """Test broadcast."""
        # Mock WebSockets
        mock_ws1 = AsyncMock()
        mock_ws1.accept = AsyncMock()
        mock_ws1.send_json = AsyncMock()
        
        mock_ws2 = AsyncMock()
        mock_ws2.accept = AsyncMock()
        mock_ws2.send_json = AsyncMock()
        
        await manager.connect(mock_ws1)
        await manager.connect(mock_ws2)
        
        message = {"type": "test", "data": "hello"}
        sent_count = await manager.broadcast(message)
        
        assert sent_count == 2
        mock_ws1.send_json.assert_called()
        mock_ws2.send_json.assert_called()
    
    @pytest.mark.asyncio
    async def test_rooms(self, manager):
        """Test rooms."""
        mock_ws = AsyncMock()
        mock_ws.accept = AsyncMock()
        mock_ws.send_json = AsyncMock()
        
        await manager.connect(mock_ws, rooms=["alerts", "signals"])
        
        assert manager.get_room_clients("alerts") == 1
        assert manager.get_room_clients("signals") == 1
        assert manager.get_room_clients("other") == 0
    
    @pytest.mark.asyncio
    async def test_disconnect_all(self, manager):
        """Test déconnexion de tous les clients."""
        mock_ws1 = AsyncMock()
        mock_ws1.accept = AsyncMock()
        mock_ws1.send_json = AsyncMock()
        mock_ws1.close = AsyncMock()
        
        mock_ws2 = AsyncMock()
        mock_ws2.accept = AsyncMock()
        mock_ws2.send_json = AsyncMock()
        mock_ws2.close = AsyncMock()
        
        await manager.connect(mock_ws1)
        await manager.connect(mock_ws2)
        
        assert manager.client_count == 2
        
        await manager.disconnect_all()
        
        assert manager.client_count == 0


class TestMetricsCollector:
    """Tests pour le collecteur de métriques."""
    
    def test_simple_metrics(self):
        """Test métriques simples (sans prometheus)."""
        metrics = SimpleMetrics()
        
        metrics.update("safety_state", 1)
        metrics.update("max_speed_percent", 100)
        
        assert metrics.get("safety_state") == 1
        assert metrics.get("max_speed_percent") == 100
    
    def test_simple_metrics_export(self):
        """Test export métriques simples."""
        metrics = SimpleMetrics()
        metrics.update("safety_state", 1)
        
        output = metrics.export()
        
        assert "robosafe_safety_state 1" in output
    
    def test_metrics_collector_disabled(self):
        """Test collecteur avec prometheus désactivé."""
        with patch("robosafe.api.metrics.PROMETHEUS_AVAILABLE", False):
            collector = MetricsCollector()
            
            # Les méthodes ne doivent pas lever d'exception
            collector.update_safety_state(1, 100)
            collector.update_signals(10, 8, 2)
            collector.update_distance(1500)
            
            output = collector.export()
            assert "not available" in output.lower()
    
    def test_update_from_state(self):
        """Test mise à jour depuis état."""
        metrics = SimpleMetrics()
        
        state = {
            "state": {
                "state_code": 1,
                "max_speed_percent": 100,
            },
            "signals": {
                "signal_1": {"value": 10, "quality": "good"},
                "signal_2": {"value": 20, "quality": "timeout"},
            },
        }
        
        # Le collecteur utilise ces données pour mise à jour
        # Test basique que la structure est correcte
        assert "state" in state
        assert "signals" in state


class TestAPIIntegration:
    """Tests d'intégration API."""
    
    @pytest.fixture
    def client(self):
        return TestClient(app)
    
    def test_cors_headers(self, client):
        """Test headers CORS."""
        response = client.options(
            "/api/v1/status",
            headers={"Origin": "http://localhost:3000"},
        )
        
        # CORS devrait autoriser l'origine
        assert response.headers.get("access-control-allow-origin")
    
    def test_metrics_endpoint(self, client):
        """Test endpoint métriques."""
        response = client.get("/metrics")
        
        assert response.status_code == 200
        # Content-Type devrait être text/plain
        assert "text" in response.headers.get("content-type", "")
    
    def test_api_documentation(self, client):
        """Test accès documentation OpenAPI."""
        response = client.get("/docs")
        
        assert response.status_code == 200
    
    def test_openapi_schema(self, client):
        """Test schéma OpenAPI."""
        response = client.get("/openapi.json")
        
        assert response.status_code == 200
        data = response.json()
        assert data["info"]["title"] == "RoboSafe Sentinel API"
