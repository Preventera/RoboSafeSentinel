"""
Tests unitaires pour les agents AgenticX5.
"""

import pytest
import asyncio
from datetime import datetime
from unittest.mock import MagicMock, AsyncMock

from robosafe.agents.base_agent import (
    BaseAgent,
    AgentConfig,
    AgentLevel,
    AgentState,
    AgentMessage,
    MessagePriority,
)
from robosafe.agents.perception_agent import (
    PerceptionAgent,
    PerceptionConfig,
    SignalQuality,
)
from robosafe.agents.analysis_agent import (
    AnalysisAgent,
    AnalysisConfig,
    RiskLevel,
)
from robosafe.agents.decision_agent import (
    DecisionAgent,
    DecisionConfig,
    ActionType,
    ActionUrgency,
)
from robosafe.agents.orchestrator_agent import (
    OrchestratorAgent,
    OrchestratorConfig,
    ExecutionStatus,
)


class TestAgentMessage:
    """Tests pour AgentMessage."""
    
    def test_message_creation(self):
        """Test création de message."""
        msg = AgentMessage(
            source="agent1",
            target="agent2",
            type="test",
            payload={"data": 123},
        )
        
        assert msg.source == "agent1"
        assert msg.target == "agent2"
        assert msg.type == "test"
        assert msg.payload["data"] == 123
        assert msg.priority == MessagePriority.NORMAL
    
    def test_message_expiration(self):
        """Test expiration de message."""
        msg = AgentMessage(
            type="test",
            ttl_seconds=0.1,
        )
        
        assert msg.is_expired is False
        
        import time
        time.sleep(0.15)
        
        assert msg.is_expired is True


class TestBaseAgent:
    """Tests pour BaseAgent."""
    
    def test_agent_levels(self):
        """Test niveaux d'agents."""
        assert AgentLevel.COLLECT == 1
        assert AgentLevel.NORMALIZE == 2
        assert AgentLevel.ANALYZE == 3
        assert AgentLevel.RECOMMEND == 4
        assert AgentLevel.ORCHESTRATE == 5
    
    def test_message_priority(self):
        """Test priorités de messages."""
        assert MessagePriority.LOW < MessagePriority.NORMAL
        assert MessagePriority.NORMAL < MessagePriority.HIGH
        assert MessagePriority.HIGH < MessagePriority.CRITICAL


class TestPerceptionAgent:
    """Tests pour PerceptionAgent."""
    
    @pytest.fixture
    def agent(self):
        return PerceptionAgent()
    
    def test_agent_initialization(self, agent):
        """Test initialisation."""
        assert agent.name == "perception"
        assert agent.level == AgentLevel.NORMALIZE
        assert agent.state == AgentState.INIT
    
    def test_signal_injection(self, agent):
        """Test injection de signaux."""
        signals = {
            "scanner_min_distance": 1500,
            "fanuc_tcp_speed": 250,
            "fumes_vlep_ratio": 0.5,
        }
        
        agent.inject_signals(signals)
        
        # Vérifier normalisation
        scanner = agent.get_signal("scanner_min_distance")
        assert scanner is not None
        assert scanner.normalized_value == 1500
        assert scanner.quality == SignalQuality.GOOD
    
    def test_signal_quality_evaluation(self, agent):
        """Test évaluation qualité."""
        # Signal dans range -> GOOD
        agent.inject_signals({"scanner_min_distance": 1500})
        sig = agent.get_signal("scanner_min_distance")
        assert sig.quality == SignalQuality.GOOD
        
        # Signal hors range -> DEGRADED
        agent.inject_signals({"scanner_min_distance": 15000})  # > max 10000
        sig = agent.get_signal("scanner_min_distance")
        assert sig.quality == SignalQuality.DEGRADED
    
    def test_sensor_callback(self, agent):
        """Test callback capteur."""
        def mock_sensor():
            return {"test_signal": 42}
        
        agent.add_sensor_callback(mock_sensor)
        
        assert len(agent._sensor_callbacks) == 1
    
    @pytest.mark.asyncio
    async def test_agent_lifecycle(self, agent):
        """Test cycle de vie."""
        await agent.start()
        assert agent.state == AgentState.RUNNING
        assert agent.is_running is True
        
        await agent.pause()
        assert agent.state == AgentState.PAUSED
        
        await agent.resume()
        assert agent.state == AgentState.RUNNING
        
        await agent.stop()
        assert agent.state == AgentState.STOPPED


class TestAnalysisAgent:
    """Tests pour AnalysisAgent."""
    
    @pytest.fixture
    def agent(self):
        return AnalysisAgent()
    
    def test_agent_initialization(self, agent):
        """Test initialisation."""
        assert agent.name == "analysis"
        assert agent.level == AgentLevel.ANALYZE
    
    def test_risk_level_ordering(self):
        """Test ordre des niveaux de risque."""
        assert RiskLevel.NONE < RiskLevel.LOW
        assert RiskLevel.LOW < RiskLevel.MEDIUM
        assert RiskLevel.MEDIUM < RiskLevel.HIGH
        assert RiskLevel.HIGH < RiskLevel.CRITICAL
    
    @pytest.mark.asyncio
    async def test_handle_signal_batch(self, agent):
        """Test traitement batch de signaux."""
        msg = AgentMessage(
            source="perception",
            type="signal_batch",
            payload={
                "signals": [
                    {
                        "id": "scanner_min_distance",
                        "value": 800,
                        "quality": "good",
                    },
                    {
                        "id": "fumes_vlep_ratio",
                        "value": 0.9,
                        "quality": "good",
                    },
                ],
            },
        )
        
        await agent.handle_message(msg)
        
        assert "scanner_min_distance" in agent._current_signals
        assert agent._current_signals["scanner_min_distance"]["value"] == 800
    
    def test_risk_score_thresholds(self, agent):
        """Test seuils de risque distance."""
        config = agent.config
        
        assert config.distance_critical_mm == 500
        assert config.distance_high_mm == 800
        assert config.distance_medium_mm == 1200
        assert config.distance_low_mm == 2000


class TestDecisionAgent:
    """Tests pour DecisionAgent."""
    
    @pytest.fixture
    def agent(self):
        return DecisionAgent()
    
    def test_agent_initialization(self, agent):
        """Test initialisation."""
        assert agent.name == "decision"
        assert agent.level == AgentLevel.RECOMMEND
    
    def test_action_types(self):
        """Test types d'actions."""
        assert ActionType.NONE < ActionType.LOG
        assert ActionType.LOG < ActionType.ALERT
        assert ActionType.ALERT < ActionType.SLOW_50
        assert ActionType.SLOW_50 < ActionType.SLOW_25
        assert ActionType.SLOW_25 < ActionType.STOP
        assert ActionType.STOP < ActionType.ESTOP
    
    def test_action_urgency(self):
        """Test niveaux d'urgence."""
        assert ActionUrgency.LOW < ActionUrgency.NORMAL
        assert ActionUrgency.NORMAL < ActionUrgency.HIGH
        assert ActionUrgency.HIGH < ActionUrgency.IMMEDIATE
    
    def test_determine_action(self, agent):
        """Test détermination action selon score."""
        # Score faible -> NONE
        action, urgency = agent._determine_action(10)
        assert action == ActionType.NONE
        
        # Score moyen -> ALERT
        action, urgency = agent._determine_action(30)
        assert action == ActionType.ALERT
        
        # Score élevé -> SLOW_50
        action, urgency = agent._determine_action(55)
        assert action == ActionType.SLOW_50
        
        # Score très élevé -> STOP
        action, urgency = agent._determine_action(85)
        assert action == ActionType.STOP
        
        # Score critique -> ESTOP
        action, urgency = agent._determine_action(98)
        assert action == ActionType.ESTOP
    
    @pytest.mark.asyncio
    async def test_handle_risk_update(self, agent):
        """Test traitement mise à jour risque."""
        msg = AgentMessage(
            source="analysis",
            type="risk_update",
            payload={
                "global_risk": {
                    "level": "HIGH",
                    "score": 75,
                    "confidence": 0.9,
                    "factors": ["distance: HIGH"],
                },
                "category_risks": {
                    "distance": {"score": 80},
                    "collision": {"score": 60},
                },
                "patterns": [],
            },
        )
        
        await agent.handle_message(msg)
        
        assert agent._global_risk["score"] == 75
        assert "distance" in agent._current_risks


class TestOrchestratorAgent:
    """Tests pour OrchestratorAgent."""
    
    @pytest.fixture
    def agent(self):
        return OrchestratorAgent()
    
    def test_agent_initialization(self, agent):
        """Test initialisation."""
        assert agent.name == "orchestrator"
        assert agent.level == AgentLevel.ORCHESTRATE
    
    def test_execution_status(self):
        """Test statuts d'exécution."""
        assert ExecutionStatus.PENDING.value == 0
        assert ExecutionStatus.SUCCESS.value == 3
        assert ExecutionStatus.FAILED.value == 4
    
    @pytest.mark.asyncio
    async def test_start_stop(self, agent):
        """Test démarrage/arrêt."""
        await agent.start()
        assert agent.is_running
        
        await agent.stop()
        assert not agent.is_running
    
    def test_register_executor(self, agent):
        """Test enregistrement exécuteur."""
        async def custom_executor(rec):
            return True
        
        agent.register_executor("CUSTOM", custom_executor)
        
        assert "CUSTOM" in agent._action_executors
    
    def test_audit_log(self, agent):
        """Test log d'audit."""
        agent._log_audit("test_event", "Test message", {"key": "value"})
        
        log = agent.get_audit_log(limit=1)
        
        assert len(log) == 1
        assert log[0]["event_type"] == "test_event"
        assert log[0]["message"] == "Test message"
    
    def test_arbitrate_recommendations(self, agent):
        """Test arbitrage recommandations."""
        agent._pending_recommendations = [
            {
                "id": "rec1",
                "action": "ALERT",
                "urgency": "NORMAL",
                "risk_score": 30,
                "received_at": datetime.now(),
            },
            {
                "id": "rec2",
                "action": "STOP",
                "urgency": "IMMEDIATE",
                "risk_score": 90,
                "received_at": datetime.now(),
            },
            {
                "id": "rec3",
                "action": "SLOW_50",
                "urgency": "HIGH",
                "risk_score": 60,
                "received_at": datetime.now(),
            },
        ]
        
        selected = agent._arbitrate_recommendations()
        
        # IMMEDIATE devrait être sélectionné
        assert selected["id"] == "rec2"
        assert selected["urgency"] == "IMMEDIATE"


class TestAgentIntegration:
    """Tests d'intégration entre agents."""
    
    @pytest.mark.asyncio
    async def test_perception_to_analysis(self):
        """Test communication Perception -> Analysis."""
        perception = PerceptionAgent()
        analysis = AnalysisAgent()
        
        messages_sent = []
        
        # Ajouter un callback capteur qui retourne des signaux
        test_signals = {
            "scanner_min_distance": 600,
            "fumes_vlep_ratio": 1.1,
        }
        perception.add_sensor_callback(lambda: test_signals)
        
        # Capturer les messages envoyés par perception
        def capture_message(msg):
            messages_sent.append(msg)
        
        perception.set_outbox_callback(capture_message)
        
        # Exécuter le cycle
        await perception.cycle()
        
        # Vérifier qu'un message a été envoyé
        assert len(messages_sent) > 0, "Perception should send signal_batch message"
        assert messages_sent[0].type == "signal_batch"
    
    @pytest.mark.asyncio
    async def test_full_pipeline(self):
        """Test pipeline complet."""
        # Créer les agents
        perception = PerceptionAgent()
        analysis = AnalysisAgent()
        decision = DecisionAgent()
        orchestrator = OrchestratorAgent()
        
        # Compteurs pour vérifier le flux
        messages_flow = {
            "perception_out": 0,
            "analysis_out": 0,
            "decision_out": 0,
        }
        
        # Ajouter un callback capteur avec scénario critique
        test_signals = {
            "scanner_min_distance": 300,  # Très proche -> CRITICAL
            "fanuc_tcp_speed": 500,        # En mouvement
            "fumes_vlep_ratio": 0.3,
        }
        perception.add_sensor_callback(lambda: test_signals)
        
        # Router les messages de manière synchrone pour les tests
        def route_from_perception(msg):
            messages_flow["perception_out"] += 1
            # Injecter directement dans analysis
            if msg.type == "signal_batch":
                for sig in msg.payload.get("signals", []):
                    analysis._current_signals[sig["id"]] = sig
        
        def route_from_analysis(msg):
            messages_flow["analysis_out"] += 1
            # Injecter directement dans decision
            if msg.type == "risk_update":
                decision._global_risk = msg.payload.get("global_risk", {})
                decision._current_risks = msg.payload.get("category_risks", {})
                decision._patterns = msg.payload.get("patterns", [])
        
        def route_from_decision(msg):
            messages_flow["decision_out"] += 1
        
        perception.set_outbox_callback(route_from_perception)
        analysis.set_outbox_callback(route_from_analysis)
        decision.set_outbox_callback(route_from_decision)
        
        # Exécuter les cycles séquentiellement
        await perception.cycle()
        await analysis.cycle()
        await decision.cycle()
        
        # Vérifier le flux de messages
        assert messages_flow["perception_out"] > 0, "Perception should send messages"
        assert messages_flow["analysis_out"] > 0, "Analysis should send risk updates"
        
        # Vérifier que des décisions ont été prises
        assert decision._decisions_made > 0, "Decision should process risks"
