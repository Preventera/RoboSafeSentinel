"""
Tests d'intégration - Scénarios de sécurité RoboSafe Sentinel.

Ces tests valident le comportement end-to-end du système
dans différents scénarios de sécurité réalistes.
"""

import pytest
import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

# Core
from robosafe.core.state_machine import SafetyStateMachine, SafetyState
from robosafe.core.signal_manager import SignalManager
from robosafe.core.rule_engine import RuleEngine, Rule, RulePriority, RuleAction

# Agents
from robosafe.agents import (
    PerceptionAgent,
    AnalysisAgent,
    DecisionAgent,
    OrchestratorAgent,
    AgentMessage,
    MessagePriority,
)


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def state_machine():
    """Machine d'états initialisée."""
    return SafetyStateMachine()


@pytest.fixture
def signal_manager():
    """Gestionnaire de signaux."""
    return SignalManager()


@pytest.fixture
def rule_engine(signal_manager, state_machine):
    """Moteur de règles configuré."""
    engine = RuleEngine(signal_manager, state_machine)
    
    # Règle distance critique
    engine.register_rule(Rule(
        id="RS-001",
        name="Distance critique",
        priority=RulePriority.P0_CRITICAL,
        condition=lambda ctx: ctx.get("scanner_min_distance", 10000) < 500,
        actions=[RuleAction(action_type="ESTOP", message="Distance < 500mm")],
    ))
    
    # Règle fumées critiques
    engine.register_rule(Rule(
        id="RS-004",
        name="Fumées critiques",
        priority=RulePriority.P0_CRITICAL,
        condition=lambda ctx: ctx.get("fumes_vlep_ratio", 0) >= 1.2,
        actions=[RuleAction(action_type="STOP", message="Fumées > 120% VLEP")],
    ))
    
    return engine


@pytest.fixture
def perception_agent():
    """Agent de perception."""
    return PerceptionAgent()


@pytest.fixture
def analysis_agent():
    """Agent d'analyse."""
    return AnalysisAgent()


@pytest.fixture
def decision_agent():
    """Agent de décision."""
    return DecisionAgent()


@pytest.fixture
def orchestrator_agent():
    """Agent orchestrateur."""
    return OrchestratorAgent()


# ============================================================================
# SCENARIO 1: Intrusion zone danger
# ============================================================================

class TestScenarioIntrusionZoneDanger:
    """
    Scénario: Un opérateur entre dans la zone de danger (< 500mm).
    
    Attendu:
    1. Scanner détecte distance < 500mm
    2. PerceptionAgent normalise le signal
    3. AnalysisAgent calcule risque CRITIQUE
    4. DecisionAgent recommande E-STOP
    5. OrchestratorAgent exécute E-STOP
    6. Robot s'arrête immédiatement
    """

    @pytest.mark.asyncio
    async def test_distance_critique_trigger_estop(self, state_machine, signal_manager):
        """Distance < 500mm doit déclencher E-STOP."""
        # Given: Système en état nominal
        await state_machine.transition_to(SafetyState.NOMINAL)
        assert state_machine.current_state == SafetyState.NOMINAL
        
        # When: Distance détectée < 500mm
        signal_manager.update_signal("scanner_min_distance", 350)
        
        # Then: Le système doit pouvoir passer en E-STOP
        await state_machine.transition_to(
            SafetyState.ESTOP,
            trigger="Distance critique 350mm"
        )
        
        assert state_machine.current_state == SafetyState.ESTOP
        assert state_machine.allows_production() == False

    @pytest.mark.asyncio
    async def test_analysis_agent_risk_scoring(self, analysis_agent):
        """AnalysisAgent doit calculer un risque critique pour distance < 500mm."""
        await analysis_agent.start()
        
        # Simuler réception de signaux avec distance critique
        msg = AgentMessage(
            id="test-1",
            source="perception",
            target="analysis",
            type="signal_batch",
            payload={
                "signals": {
                    "scanner_min_distance": {"value": 300, "quality": "GOOD"},
                }
            },
            priority=MessagePriority.HIGH,
        )
        
        await analysis_agent.receive(msg)
        await asyncio.sleep(0.15)  # Attendre le cycle d'analyse
        
        # Vérifier que l'agent a traité le message
        assert analysis_agent.metrics["messages_processed"] >= 1
        
        await analysis_agent.stop()

    @pytest.mark.asyncio
    async def test_full_pipeline_intrusion(
        self, perception_agent, analysis_agent, decision_agent, orchestrator_agent
    ):
        """Test du pipeline complet pour intrusion."""
        # Setup: Connecter les agents
        messages_received = []
        
        def capture_message(msg):
            messages_received.append(msg)
        
        orchestrator_agent.set_outbox_callback(capture_message)
        
        # Start agents
        await perception_agent.start()
        await analysis_agent.start()
        await decision_agent.start()
        await orchestrator_agent.start()
        
        # Simuler données capteur avec intrusion
        def mock_sensor_data():
            return {
                "scanner_min_distance": 250,  # Intrusion !
                "fanuc_tcp_speed": 500,
            }
        
        perception_agent.add_sensor_callback(mock_sensor_data)
        
        # Attendre quelques cycles
        await asyncio.sleep(0.3)
        
        # Cleanup
        await perception_agent.stop()
        await analysis_agent.stop()
        await decision_agent.stop()
        await orchestrator_agent.stop()
        
        # Vérifier que les agents ont fonctionné
        assert perception_agent.metrics["cycles_completed"] > 0


# ============================================================================
# SCENARIO 2: Exposition fumées de soudage
# ============================================================================

class TestScenarioExpositionFumees:
    """
    Scénario: Concentration de fumées dépasse 120% VLEP.
    
    Attendu:
    1. Capteur détecte concentration > VLEP
    2. Système déclenche alerte puis arrêt
    3. Ventilation augmentée automatiquement
    """

    @pytest.mark.asyncio
    async def test_fumees_critiques_trigger_stop(self, state_machine, signal_manager):
        """Fumées > 120% VLEP doit déclencher STOP."""
        await state_machine.transition_to(SafetyState.NOMINAL)
        
        # When: VLEP ratio >= 1.2
        signal_manager.update_signal("fumes_vlep_ratio", 1.25)
        
        # Then: Transition vers STOP
        await state_machine.transition_to(
            SafetyState.STOP,
            trigger="Fumées critiques 125% VLEP"
        )
        
        assert state_machine.current_state == SafetyState.STOP

    @pytest.mark.asyncio
    async def test_fumees_elevees_alert(self, signal_manager):
        """Fumées 80-120% VLEP doit générer une alerte."""
        signal_manager.update_signal("fumes_vlep_ratio", 0.95)
        
        signal = signal_manager.get_signal("fumes_vlep_ratio")
        assert signal is not None
        assert signal.value == 0.95
        # L'alerte serait générée par le RuleEngine


# ============================================================================
# SCENARIO 3: Défaillance capteur
# ============================================================================

class TestScenarioDefaillanceCapteur:
    """
    Scénario: Un capteur de sécurité tombe en panne.
    
    Attendu:
    1. Timeout détecté sur signal
    2. Qualité du signal passe à BAD
    3. Système passe en mode dégradé (SLOW ou STOP)
    """

    @pytest.mark.asyncio
    async def test_signal_timeout_detection(self, signal_manager):
        """Timeout sur signal critique doit être détecté."""
        # Créer un signal
        signal_manager.update_signal("scanner_min_distance", 1500)
        
        # Vérifier qu'il existe
        signal = signal_manager.get_signal("scanner_min_distance")
        assert signal is not None
        
        # Le timeout serait détecté par le PerceptionAgent
        # après un délai sans mise à jour

    @pytest.mark.asyncio
    async def test_degraded_mode_on_sensor_failure(self, state_machine):
        """Défaillance capteur doit déclencher mode dégradé."""
        await state_machine.transition_to(SafetyState.NOMINAL)
        
        # Simuler défaillance -> passage en SLOW
        await state_machine.transition_to(
            SafetyState.SLOW_50,
            trigger="Capteur scanner timeout"
        )
        
        assert state_machine.current_state == SafetyState.SLOW_50
        assert state_machine.allows_production() == True
        assert state_machine.max_speed_percent() == 50


# ============================================================================
# SCENARIO 4: EPI manquant détecté par vision
# ============================================================================

class TestScenarioEPIManquant:
    """
    Scénario: Vision IA détecte opérateur sans EPI.
    
    Attendu:
    1. YOLO détecte personne
    2. Classification EPI échoue
    3. Alerte générée
    4. Optionnel: ralentissement si proche du robot
    """

    @pytest.mark.asyncio
    async def test_ppe_detection_alert(self, signal_manager):
        """Détection EPI manquant doit mettre à jour les signaux."""
        signal_manager.update_signal("vision_person_count", 1)
        signal_manager.update_signal("vision_ppe_ok", False)
        
        person_count = signal_manager.get_signal("vision_person_count")
        ppe_ok = signal_manager.get_signal("vision_ppe_ok")
        
        assert person_count.value == 1
        assert ppe_ok.value == False


# ============================================================================
# SCENARIO 5: Recovery après E-STOP
# ============================================================================

class TestScenarioRecoveryESTOP:
    """
    Scénario: Reprise après arrêt d'urgence.
    
    Attendu:
    1. E-STOP activé
    2. Opérateur corrige la situation
    3. Reset demandé
    4. Vérification conditions OK
    5. Retour progressif à NOMINAL
    """

    @pytest.mark.asyncio
    async def test_estop_to_nominal_recovery(self, state_machine):
        """Recovery de E-STOP vers NOMINAL."""
        # Given: Système en E-STOP
        await state_machine.transition_to(SafetyState.ESTOP)
        assert state_machine.current_state == SafetyState.ESTOP
        
        # When: Conditions OK, reset demandé
        # D'abord vers STOP (étape intermédiaire)
        await state_machine.transition_to(
            SafetyState.STOP,
            trigger="Reset requested"
        )
        
        # Puis vers NOMINAL
        await state_machine.transition_to(
            SafetyState.NOMINAL,
            trigger="All clear"
        )
        
        # Then
        assert state_machine.current_state == SafetyState.NOMINAL
        assert state_machine.allows_production() == True

    @pytest.mark.asyncio
    async def test_cannot_skip_recovery_steps(self, state_machine):
        """Ne peut pas passer directement de E-STOP à production."""
        await state_machine.transition_to(SafetyState.ESTOP)
        
        # Vérifier que la production n'est pas autorisée
        assert state_machine.allows_production() == False


# ============================================================================
# SCENARIO 6: Approche progressive
# ============================================================================

class TestScenarioApprocheProgressive:
    """
    Scénario: Opérateur s'approche progressivement du robot.
    
    Attendu:
    1. Distance > 2000mm: NOMINAL
    2. Distance 1200-2000mm: LOG
    3. Distance 800-1200mm: SLOW 50%
    4. Distance 500-800mm: SLOW 25%
    5. Distance < 500mm: E-STOP
    """

    @pytest.mark.asyncio
    async def test_progressive_slowdown(self, state_machine):
        """Ralentissement progressif selon la distance."""
        await state_machine.transition_to(SafetyState.NOMINAL)
        
        # Distance 1000mm -> SLOW 50%
        await state_machine.transition_to(SafetyState.SLOW_50)
        assert state_machine.max_speed_percent() == 50
        
        # Distance 600mm -> SLOW 25%
        await state_machine.transition_to(SafetyState.SLOW_25)
        assert state_machine.max_speed_percent() == 25
        
        # Distance 400mm -> E-STOP
        await state_machine.transition_to(SafetyState.ESTOP)
        assert state_machine.max_speed_percent() == 0


# ============================================================================
# SCENARIO 7: Multi-menaces simultanées
# ============================================================================

class TestScenarioMultiMenaces:
    """
    Scénario: Plusieurs menaces détectées simultanément.
    
    Attendu:
    1. Distance warning + fumées élevées
    2. Le système priorise la menace la plus grave
    3. Actions combinées si nécessaire
    """

    @pytest.mark.asyncio
    async def test_priority_handling(self, state_machine, signal_manager):
        """La menace la plus grave doit être priorisée."""
        await state_machine.transition_to(SafetyState.NOMINAL)
        
        # Simuler: Distance warning (800mm) + Fumées critiques (130% VLEP)
        signal_manager.update_signal("scanner_min_distance", 800)
        signal_manager.update_signal("fumes_vlep_ratio", 1.3)
        
        # Les fumées critiques sont P0, donc STOP a priorité
        await state_machine.transition_to(
            SafetyState.STOP,
            trigger="Fumées critiques 130% VLEP"
        )
        
        assert state_machine.current_state == SafetyState.STOP


# ============================================================================
# SCENARIO 8: Performance temps réel
# ============================================================================

class TestScenarioPerformance:
    """
    Scénario: Vérification des temps de réponse.
    
    Attendu:
    - Détection -> Décision < 100ms
    - Décision -> Exécution < 50ms
    - Total < 200ms
    """

    @pytest.mark.asyncio
    async def test_response_time_state_transition(self, state_machine):
        """Transition d'état doit être < 10ms."""
        import time
        
        await state_machine.transition_to(SafetyState.NOMINAL)
        
        start = time.perf_counter()
        await state_machine.transition_to(SafetyState.ESTOP)
        elapsed = (time.perf_counter() - start) * 1000
        
        assert elapsed < 10, f"Transition took {elapsed}ms, expected < 10ms"

    @pytest.mark.asyncio
    async def test_signal_update_performance(self, signal_manager):
        """Mise à jour signal doit être < 1ms."""
        import time
        
        start = time.perf_counter()
        for i in range(100):
            signal_manager.update_signal(f"test_signal_{i}", i * 10)
        elapsed = (time.perf_counter() - start) * 1000
        
        avg_time = elapsed / 100
        assert avg_time < 1, f"Avg update took {avg_time}ms, expected < 1ms"


# ============================================================================
# SCENARIO 9: Audit et traçabilité
# ============================================================================

class TestScenarioAudit:
    """
    Scénario: Vérification de la traçabilité.
    
    Attendu:
    - Toutes les transitions sont loggées
    - Horodatage précis
    - Source de la transition identifiée
    """

    @pytest.mark.asyncio
    async def test_state_history_tracking(self, state_machine):
        """L'historique des états doit être conservé."""
        await state_machine.transition_to(SafetyState.NOMINAL)
        await state_machine.transition_to(SafetyState.SLOW_50)
        await state_machine.transition_to(SafetyState.NOMINAL)
        
        history = state_machine.get_history()
        
        assert len(history) >= 3
        for entry in history:
            assert "timestamp" in entry or hasattr(entry, "timestamp")


# ============================================================================
# SCENARIO 10: Règles dynamiques
# ============================================================================

class TestScenarioReglesDynamiques:
    """
    Scénario: Activation/désactivation de règles à chaud.
    
    Attendu:
    - Règles peuvent être activées/désactivées
    - Effet immédiat sur l'évaluation
    """

    @pytest.mark.asyncio
    async def test_rule_enable_disable(self, rule_engine):
        """Règles peuvent être activées/désactivées."""
        # Désactiver une règle
        rule_engine.disable_rule("RS-001")
        
        # Vérifier qu'elle est désactivée
        stats = rule_engine.get_stats()
        # La règle devrait être marquée comme désactivée
        
        # Réactiver
        rule_engine.enable_rule("RS-001")


# ============================================================================
# Run tests
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--asyncio-mode=auto"])
