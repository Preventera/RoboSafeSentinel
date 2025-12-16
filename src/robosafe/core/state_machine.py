"""
Machine d'états de sécurité RoboSafe.

Gère les transitions entre états de sécurité:
- NORMAL: Fonctionnement nominal
- WARNING: Alerte active, surveillance renforcée
- SLOW: Vitesse réduite (25% ou 50%)
- STOP: Arrêt contrôlé (CAT.1)
- ESTOP: Arrêt d'urgence (CAT.0)
- RECOVERY: Reprise progressive après arrêt
- FALLBACK: Mode dégradé (sécurité PLC seule)
"""

from enum import Enum, auto
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Callable, Dict, List
import asyncio
import structlog

logger = structlog.get_logger(__name__)


class SafetyState(Enum):
    """États de sécurité possibles."""
    
    INIT = auto()       # Initialisation
    NORMAL = auto()     # Fonctionnement normal (vert)
    WARNING = auto()    # Alerte active (orange)
    SLOW_50 = auto()    # Vitesse 50% (jaune)
    SLOW_25 = auto()    # Vitesse 25% (jaune clignotant)
    STOP = auto()       # Arrêt contrôlé CAT.1 (rouge)
    ESTOP = auto()      # Arrêt urgence CAT.0 (rouge clignotant)
    RECOVERY = auto()   # Reprise progressive (bleu)
    FALLBACK = auto()   # Mode dégradé (violet)
    
    @property
    def code(self) -> int:
        """Code numérique pour protocole."""
        codes = {
            SafetyState.INIT: 0x00,
            SafetyState.NORMAL: 0x01,
            SafetyState.WARNING: 0x02,
            SafetyState.SLOW_50: 0x03,
            SafetyState.SLOW_25: 0x04,
            SafetyState.STOP: 0x10,
            SafetyState.ESTOP: 0xFF,
            SafetyState.RECOVERY: 0x20,
            SafetyState.FALLBACK: 0xF0,
        }
        return codes.get(self, 0xFF)
    
    @property
    def max_speed_percent(self) -> int:
        """Vitesse maximale autorisée en %."""
        speeds = {
            SafetyState.INIT: 0,
            SafetyState.NORMAL: 100,
            SafetyState.WARNING: 100,
            SafetyState.SLOW_50: 50,
            SafetyState.SLOW_25: 25,
            SafetyState.STOP: 0,
            SafetyState.ESTOP: 0,
            SafetyState.RECOVERY: 10,
            SafetyState.FALLBACK: 50,
        }
        return speeds.get(self, 0)
    
    @property
    def allows_production(self) -> bool:
        """Indique si la production est autorisée."""
        return self in (SafetyState.NORMAL, SafetyState.WARNING, 
                       SafetyState.SLOW_50, SafetyState.SLOW_25)


@dataclass
class StateTransition:
    """Représente une transition d'état."""
    
    from_state: SafetyState
    to_state: SafetyState
    timestamp: datetime = field(default_factory=datetime.now)
    trigger: str = ""
    rule_id: Optional[str] = None
    data: Dict = field(default_factory=dict)
    
    def to_dict(self) -> Dict:
        """Convertit en dictionnaire pour logging."""
        return {
            "from_state": self.from_state.name,
            "to_state": self.to_state.name,
            "timestamp": self.timestamp.isoformat(),
            "trigger": self.trigger,
            "rule_id": self.rule_id,
            "data": self.data,
        }


class SafetyStateMachine:
    """
    Machine d'états de sécurité.
    
    Gère les transitions entre états selon les règles de sécurité définies.
    Garantit que les transitions sont valides et tracées.
    
    Attributes:
        current_state: État actuel
        previous_state: État précédent
        history: Historique des transitions
    """
    
    # Transitions valides (from_state -> [to_states])
    VALID_TRANSITIONS: Dict[SafetyState, List[SafetyState]] = {
        SafetyState.INIT: [SafetyState.NORMAL, SafetyState.FALLBACK, SafetyState.ESTOP],
        SafetyState.NORMAL: [SafetyState.WARNING, SafetyState.SLOW_50, SafetyState.SLOW_25, 
                            SafetyState.STOP, SafetyState.ESTOP, SafetyState.FALLBACK],
        SafetyState.WARNING: [SafetyState.NORMAL, SafetyState.SLOW_50, SafetyState.SLOW_25,
                             SafetyState.STOP, SafetyState.ESTOP, SafetyState.FALLBACK],
        SafetyState.SLOW_50: [SafetyState.NORMAL, SafetyState.WARNING, SafetyState.SLOW_25,
                             SafetyState.STOP, SafetyState.ESTOP, SafetyState.FALLBACK],
        SafetyState.SLOW_25: [SafetyState.NORMAL, SafetyState.WARNING, SafetyState.SLOW_50,
                             SafetyState.STOP, SafetyState.ESTOP, SafetyState.FALLBACK],
        SafetyState.STOP: [SafetyState.RECOVERY, SafetyState.ESTOP, SafetyState.FALLBACK],
        SafetyState.ESTOP: [SafetyState.RECOVERY],  # Reset manuel obligatoire
        SafetyState.RECOVERY: [SafetyState.NORMAL, SafetyState.STOP, SafetyState.ESTOP, 
                              SafetyState.FALLBACK],
        SafetyState.FALLBACK: [SafetyState.NORMAL, SafetyState.RECOVERY, SafetyState.ESTOP],
    }
    
    def __init__(
        self,
        initial_state: SafetyState = SafetyState.INIT,
        on_transition: Optional[Callable[[StateTransition], None]] = None,
        max_history: int = 1000,
    ):
        """
        Initialise la machine d'états.
        
        Args:
            initial_state: État initial
            on_transition: Callback appelé à chaque transition
            max_history: Taille max de l'historique
        """
        self._current_state = initial_state
        self._previous_state: Optional[SafetyState] = None
        self._on_transition = on_transition
        self._max_history = max_history
        self._history: List[StateTransition] = []
        self._state_entered_at = datetime.now()
        self._lock = asyncio.Lock()
        
        logger.info(
            "state_machine_initialized",
            initial_state=initial_state.name
        )
    
    @property
    def current_state(self) -> SafetyState:
        """État actuel."""
        return self._current_state
    
    @property
    def previous_state(self) -> Optional[SafetyState]:
        """État précédent."""
        return self._previous_state
    
    @property
    def state_duration_seconds(self) -> float:
        """Durée dans l'état actuel en secondes."""
        return (datetime.now() - self._state_entered_at).total_seconds()
    
    @property
    def history(self) -> List[StateTransition]:
        """Historique des transitions (copie)."""
        return self._history.copy()
    
    def can_transition_to(self, target_state: SafetyState) -> bool:
        """
        Vérifie si une transition vers l'état cible est valide.
        
        Args:
            target_state: État cible
            
        Returns:
            True si la transition est valide
        """
        if self._current_state == target_state:
            return True  # Pas de changement
        
        valid_targets = self.VALID_TRANSITIONS.get(self._current_state, [])
        return target_state in valid_targets
    
    async def transition_to(
        self,
        target_state: SafetyState,
        trigger: str = "",
        rule_id: Optional[str] = None,
        data: Optional[Dict] = None,
        force: bool = False,
    ) -> bool:
        """
        Effectue une transition vers un nouvel état.
        
        Args:
            target_state: État cible
            trigger: Description du déclencheur
            rule_id: ID de la règle ayant causé la transition
            data: Données additionnelles
            force: Force la transition (DANGER - usage interne)
            
        Returns:
            True si la transition a été effectuée
        """
        async with self._lock:
            # Même état = pas de transition
            if self._current_state == target_state:
                return True
            
            # Vérifier validité
            if not force and not self.can_transition_to(target_state):
                logger.warning(
                    "invalid_transition_attempt",
                    from_state=self._current_state.name,
                    to_state=target_state.name,
                    trigger=trigger,
                )
                return False
            
            # Effectuer la transition
            transition = StateTransition(
                from_state=self._current_state,
                to_state=target_state,
                trigger=trigger,
                rule_id=rule_id,
                data=data or {},
            )
            
            self._previous_state = self._current_state
            self._current_state = target_state
            self._state_entered_at = datetime.now()
            
            # Historique
            self._history.append(transition)
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history:]
            
            # Log
            logger.info(
                "state_transition",
                from_state=transition.from_state.name,
                to_state=transition.to_state.name,
                trigger=trigger,
                rule_id=rule_id,
            )
            
            # Callback
            if self._on_transition:
                try:
                    self._on_transition(transition)
                except Exception as e:
                    logger.error("transition_callback_error", error=str(e))
            
            return True
    
    async def request_estop(self, trigger: str, rule_id: Optional[str] = None) -> bool:
        """
        Demande un arrêt d'urgence (priorité maximale).
        
        Args:
            trigger: Cause de l'E-STOP
            rule_id: ID de la règle
            
        Returns:
            Toujours True (E-STOP toujours accepté)
        """
        return await self.transition_to(
            SafetyState.ESTOP,
            trigger=trigger,
            rule_id=rule_id,
            force=True,  # E-STOP toujours accepté
        )
    
    async def request_stop(self, trigger: str, rule_id: Optional[str] = None) -> bool:
        """Demande un arrêt contrôlé (CAT.1)."""
        return await self.transition_to(
            SafetyState.STOP,
            trigger=trigger,
            rule_id=rule_id,
        )
    
    async def request_slow(
        self, 
        speed_percent: int, 
        trigger: str, 
        rule_id: Optional[str] = None
    ) -> bool:
        """
        Demande un ralentissement.
        
        Args:
            speed_percent: 25 ou 50
            trigger: Cause
            rule_id: ID règle
        """
        target = SafetyState.SLOW_25 if speed_percent <= 25 else SafetyState.SLOW_50
        return await self.transition_to(target, trigger=trigger, rule_id=rule_id)
    
    async def request_recovery(self, trigger: str = "reset_acknowledged") -> bool:
        """Demande passage en mode récupération."""
        return await self.transition_to(SafetyState.RECOVERY, trigger=trigger)
    
    async def request_normal(self, trigger: str = "all_clear") -> bool:
        """Demande retour à l'état normal."""
        return await self.transition_to(SafetyState.NORMAL, trigger=trigger)
    
    async def enter_fallback(self, trigger: str = "ia_comm_lost") -> bool:
        """Entre en mode dégradé (fallback PLC)."""
        return await self.transition_to(
            SafetyState.FALLBACK,
            trigger=trigger,
            force=True,  # Fallback toujours accepté
        )
    
    def get_status(self) -> Dict:
        """Retourne le statut actuel."""
        return {
            "current_state": self._current_state.name,
            "state_code": self._current_state.code,
            "previous_state": self._previous_state.name if self._previous_state else None,
            "max_speed_percent": self._current_state.max_speed_percent,
            "allows_production": self._current_state.allows_production,
            "state_duration_seconds": self.state_duration_seconds,
            "transition_count": len(self._history),
        }
