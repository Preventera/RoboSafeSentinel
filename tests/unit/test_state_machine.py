"""
Tests unitaires pour la machine d'états de sécurité.
"""

import pytest
from datetime import datetime

from robosafe.core.state_machine import (
    SafetyState,
    SafetyStateMachine,
    StateTransition,
)


class TestSafetyState:
    """Tests pour l'enum SafetyState."""
    
    def test_state_codes(self):
        """Vérifie les codes d'état."""
        assert SafetyState.NORMAL.code == 0x01
        assert SafetyState.ESTOP.code == 0xFF
        assert SafetyState.STOP.code == 0x10
    
    def test_max_speed_percent(self):
        """Vérifie les vitesses maximales."""
        assert SafetyState.NORMAL.max_speed_percent == 100
        assert SafetyState.SLOW_50.max_speed_percent == 50
        assert SafetyState.SLOW_25.max_speed_percent == 25
        assert SafetyState.STOP.max_speed_percent == 0
        assert SafetyState.ESTOP.max_speed_percent == 0
        assert SafetyState.RECOVERY.max_speed_percent == 10
    
    def test_allows_production(self):
        """Vérifie quels états permettent la production."""
        assert SafetyState.NORMAL.allows_production is True
        assert SafetyState.WARNING.allows_production is True
        assert SafetyState.SLOW_50.allows_production is True
        assert SafetyState.SLOW_25.allows_production is True
        assert SafetyState.STOP.allows_production is False
        assert SafetyState.ESTOP.allows_production is False
        assert SafetyState.RECOVERY.allows_production is False


class TestStateTransition:
    """Tests pour StateTransition."""
    
    def test_transition_creation(self):
        """Vérifie la création d'une transition."""
        transition = StateTransition(
            from_state=SafetyState.NORMAL,
            to_state=SafetyState.STOP,
            trigger="intrusion_detected",
            rule_id="RS-010",
        )
        
        assert transition.from_state == SafetyState.NORMAL
        assert transition.to_state == SafetyState.STOP
        assert transition.trigger == "intrusion_detected"
        assert transition.rule_id == "RS-010"
        assert isinstance(transition.timestamp, datetime)
    
    def test_to_dict(self):
        """Vérifie la conversion en dictionnaire."""
        transition = StateTransition(
            from_state=SafetyState.NORMAL,
            to_state=SafetyState.WARNING,
            trigger="fumes_high",
        )
        
        d = transition.to_dict()
        
        assert d["from_state"] == "NORMAL"
        assert d["to_state"] == "WARNING"
        assert d["trigger"] == "fumes_high"
        assert "timestamp" in d


class TestSafetyStateMachine:
    """Tests pour SafetyStateMachine."""
    
    @pytest.fixture
    def state_machine(self):
        """Crée une machine d'états pour les tests."""
        return SafetyStateMachine(initial_state=SafetyState.INIT)
    
    def test_initial_state(self, state_machine):
        """Vérifie l'état initial."""
        assert state_machine.current_state == SafetyState.INIT
        assert state_machine.previous_state is None
    
    @pytest.mark.asyncio
    async def test_valid_transition(self, state_machine):
        """Vérifie une transition valide."""
        result = await state_machine.transition_to(
            SafetyState.NORMAL,
            trigger="init_complete",
        )
        
        assert result is True
        assert state_machine.current_state == SafetyState.NORMAL
        assert state_machine.previous_state == SafetyState.INIT
    
    @pytest.mark.asyncio
    async def test_invalid_transition(self, state_machine):
        """Vérifie qu'une transition invalide est rejetée."""
        # De INIT, on ne peut pas aller directement à RECOVERY
        result = await state_machine.transition_to(
            SafetyState.RECOVERY,
            trigger="invalid",
        )
        
        assert result is False
        assert state_machine.current_state == SafetyState.INIT
    
    @pytest.mark.asyncio
    async def test_estop_always_accepted(self, state_machine):
        """Vérifie que E-STOP est toujours accepté."""
        await state_machine.transition_to(SafetyState.NORMAL, trigger="init")
        
        result = await state_machine.request_estop(trigger="emergency")
        
        assert result is True
        assert state_machine.current_state == SafetyState.ESTOP
    
    @pytest.mark.asyncio
    async def test_transition_history(self, state_machine):
        """Vérifie l'historique des transitions."""
        await state_machine.transition_to(SafetyState.NORMAL, trigger="init")
        await state_machine.transition_to(SafetyState.WARNING, trigger="alert")
        await state_machine.transition_to(SafetyState.SLOW_50, trigger="proximity")
        
        history = state_machine.history
        
        assert len(history) == 3
        assert history[0].to_state == SafetyState.NORMAL
        assert history[1].to_state == SafetyState.WARNING
        assert history[2].to_state == SafetyState.SLOW_50
    
    @pytest.mark.asyncio
    async def test_same_state_no_transition(self, state_machine):
        """Vérifie qu'aller vers le même état ne crée pas de transition."""
        await state_machine.transition_to(SafetyState.NORMAL, trigger="init")
        
        initial_history_len = len(state_machine.history)
        
        result = await state_machine.transition_to(SafetyState.NORMAL, trigger="same")
        
        assert result is True
        assert len(state_machine.history) == initial_history_len
    
    @pytest.mark.asyncio
    async def test_request_stop(self, state_machine):
        """Vérifie la demande d'arrêt."""
        await state_machine.transition_to(SafetyState.NORMAL, trigger="init")
        
        result = await state_machine.request_stop(trigger="intrusion")
        
        assert result is True
        assert state_machine.current_state == SafetyState.STOP
    
    @pytest.mark.asyncio
    async def test_request_slow(self, state_machine):
        """Vérifie la demande de ralentissement."""
        await state_machine.transition_to(SafetyState.NORMAL, trigger="init")
        
        result = await state_machine.request_slow(50, trigger="proximity")
        assert state_machine.current_state == SafetyState.SLOW_50
        
        await state_machine.transition_to(SafetyState.NORMAL, trigger="clear")
        
        result = await state_machine.request_slow(25, trigger="close_proximity")
        assert state_machine.current_state == SafetyState.SLOW_25
    
    @pytest.mark.asyncio
    async def test_recovery_to_normal(self, state_machine):
        """Vérifie le cycle STOP -> RECOVERY -> NORMAL."""
        await state_machine.transition_to(SafetyState.NORMAL, trigger="init")
        await state_machine.request_stop(trigger="intrusion")
        
        assert state_machine.current_state == SafetyState.STOP
        
        await state_machine.request_recovery(trigger="reset")
        assert state_machine.current_state == SafetyState.RECOVERY
        
        await state_machine.request_normal(trigger="all_clear")
        assert state_machine.current_state == SafetyState.NORMAL
    
    @pytest.mark.asyncio
    async def test_estop_requires_manual_reset(self, state_machine):
        """Vérifie que E-STOP ne peut aller que vers RECOVERY."""
        await state_machine.transition_to(SafetyState.NORMAL, trigger="init")
        await state_machine.request_estop(trigger="emergency")
        
        # Ne peut pas aller directement vers NORMAL
        result = await state_machine.transition_to(SafetyState.NORMAL, trigger="try")
        assert result is False
        
        # Doit passer par RECOVERY
        result = await state_machine.request_recovery(trigger="manual_reset")
        assert result is True
        assert state_machine.current_state == SafetyState.RECOVERY
    
    @pytest.mark.asyncio
    async def test_fallback_mode(self, state_machine):
        """Vérifie l'entrée en mode fallback."""
        await state_machine.transition_to(SafetyState.NORMAL, trigger="init")
        
        result = await state_machine.enter_fallback(trigger="ia_comm_lost")
        
        assert result is True
        assert state_machine.current_state == SafetyState.FALLBACK
    
    def test_get_status(self, state_machine):
        """Vérifie le statut retourné."""
        status = state_machine.get_status()
        
        assert "current_state" in status
        assert "state_code" in status
        assert "max_speed_percent" in status
        assert "allows_production" in status
        assert "state_duration_seconds" in status
    
    @pytest.mark.asyncio
    async def test_callback_on_transition(self):
        """Vérifie que le callback est appelé."""
        transitions_received = []
        
        def callback(transition):
            transitions_received.append(transition)
        
        sm = SafetyStateMachine(
            initial_state=SafetyState.INIT,
            on_transition=callback,
        )
        
        await sm.transition_to(SafetyState.NORMAL, trigger="init")
        
        assert len(transitions_received) == 1
        assert transitions_received[0].to_state == SafetyState.NORMAL
