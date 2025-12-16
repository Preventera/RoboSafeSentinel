"""
Moteur de règles d'intervention RoboSafe.

Évalue les conditions en temps réel et déclenche les actions appropriées:
- P0 (Critique): E-STOP immédiat (<100ms)
- P1 (Haute): Arrêt contrôlé (<500ms)
- P2 (Moyenne): Ralentissement (<1s)
- P3 (Basse): Alertes (<5s)
- P4 (Diagnostic): Maintenance
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
import asyncio
import structlog

from robosafe.core.signal_manager import SignalManager, Signal
from robosafe.core.state_machine import SafetyStateMachine, SafetyState

logger = structlog.get_logger(__name__)


class RulePriority(Enum):
    """Priorité des règles."""
    P0_CRITICAL = 0    # E-STOP immédiat
    P1_HIGH = 1        # Arrêt contrôlé
    P2_MEDIUM = 2      # Ralentissement
    P3_LOW = 3         # Alertes
    P4_DIAGNOSTIC = 4  # Maintenance
    
    @property
    def max_latency_ms(self) -> int:
        """Latence maximale autorisée."""
        latencies = {
            RulePriority.P0_CRITICAL: 100,
            RulePriority.P1_HIGH: 500,
            RulePriority.P2_MEDIUM: 1000,
            RulePriority.P3_LOW: 5000,
            RulePriority.P4_DIAGNOSTIC: 10000,
        }
        return latencies.get(self, 10000)


class ActionType(Enum):
    """Types d'actions possibles."""
    ESTOP = "estop"              # Arrêt urgence CAT.0
    STOP_CAT1 = "stop_cat1"      # Arrêt contrôlé CAT.1
    SLOW_50 = "slow_50"          # Vitesse 50%
    SLOW_25 = "slow_25"          # Vitesse 25%
    ALERT = "alert"              # Alerte
    LOG = "log"                  # Journalisation
    SET_DEGRADED = "set_degraded"  # Mode dégradé
    BLOCK_RESET = "block_reset"  # Bloquer reset
    INCREASE_MARGIN = "increase_margin"  # Augmenter marges


@dataclass
class RuleAction:
    """Action à exécuter."""
    action_type: ActionType
    target: Optional[str] = None  # Destinataire alerte
    message: Optional[str] = None
    data: Dict = field(default_factory=dict)


@dataclass
class RuleResult:
    """Résultat d'évaluation d'une règle."""
    rule_id: str
    triggered: bool
    timestamp: datetime = field(default_factory=datetime.now)
    condition_values: Dict[str, Any] = field(default_factory=dict)
    actions_executed: List[ActionType] = field(default_factory=list)
    execution_time_ms: float = 0.0
    error: Optional[str] = None


@dataclass
class Rule:
    """Définition d'une règle d'intervention."""
    
    id: str
    name: str
    priority: RulePriority
    condition: Callable[[Dict[str, Any]], bool]
    actions: List[RuleAction]
    description: str = ""
    enabled: bool = True
    cooldown_ms: float = 0.0  # Temps min entre deux déclenchements
    
    # Signaux requis pour évaluation
    required_signals: List[str] = field(default_factory=list)
    
    # État interne
    _last_triggered: Optional[datetime] = field(default=None, repr=False)
    _trigger_count: int = field(default=0, repr=False)
    
    def can_trigger(self) -> bool:
        """Vérifie si la règle peut être déclenchée (cooldown)."""
        if self.cooldown_ms <= 0 or self._last_triggered is None:
            return True
        
        elapsed = (datetime.now() - self._last_triggered).total_seconds() * 1000
        return elapsed >= self.cooldown_ms
    
    def mark_triggered(self) -> None:
        """Marque la règle comme déclenchée."""
        self._last_triggered = datetime.now()
        self._trigger_count += 1


class RuleEngine:
    """
    Moteur de règles temps réel.
    
    Évalue continuellement les règles et déclenche les actions.
    Les règles sont évaluées par ordre de priorité (P0 en premier).
    """
    
    def __init__(
        self,
        signal_manager: SignalManager,
        state_machine: SafetyStateMachine,
        evaluation_interval_ms: float = 10.0,
    ):
        """
        Initialise le moteur de règles.
        
        Args:
            signal_manager: Gestionnaire de signaux
            state_machine: Machine d'états
            evaluation_interval_ms: Intervalle d'évaluation
        """
        self._signal_manager = signal_manager
        self._state_machine = state_machine
        self._evaluation_interval = evaluation_interval_ms / 1000.0
        
        self._rules: Dict[str, Rule] = {}
        self._rules_by_priority: Dict[RulePriority, List[Rule]] = {
            p: [] for p in RulePriority
        }
        
        self._running = False
        self._eval_task: Optional[asyncio.Task] = None
        self._results_history: List[RuleResult] = []
        self._max_history = 10000
        
        # Callbacks
        self._on_rule_triggered: List[Callable[[RuleResult], None]] = []
        self._on_action_executed: List[Callable[[str, RuleAction], None]] = []
        
        # Stats
        self._eval_count = 0
        self._trigger_count = 0
        self._error_count = 0
        
        logger.info("rule_engine_initialized")
    
    def register_rule(self, rule: Rule) -> None:
        """
        Enregistre une règle.
        
        Args:
            rule: Règle à enregistrer
        """
        self._rules[rule.id] = rule
        self._rules_by_priority[rule.priority].append(rule)
        
        logger.info(
            "rule_registered",
            rule_id=rule.id,
            priority=rule.priority.name,
        )
    
    def register_rules(self, rules: List[Rule]) -> None:
        """Enregistre plusieurs règles."""
        for rule in rules:
            self.register_rule(rule)
    
    def enable_rule(self, rule_id: str) -> bool:
        """Active une règle."""
        if rule_id in self._rules:
            self._rules[rule_id].enabled = True
            logger.info("rule_enabled", rule_id=rule_id)
            return True
        return False
    
    def disable_rule(self, rule_id: str) -> bool:
        """Désactive une règle."""
        if rule_id in self._rules:
            self._rules[rule_id].enabled = False
            logger.info("rule_disabled", rule_id=rule_id)
            return True
        return False
    
    def get_signal_values(self) -> Dict[str, Any]:
        """Récupère toutes les valeurs de signaux pour évaluation."""
        signals = self._signal_manager.get_all_signals()
        
        return {
            signal_id: signal.value
            for signal_id, signal in signals.items()
        }
    
    async def evaluate_rule(self, rule: Rule) -> RuleResult:
        """
        Évalue une règle unique.
        
        Args:
            rule: Règle à évaluer
            
        Returns:
            Résultat d'évaluation
        """
        start_time = datetime.now()
        result = RuleResult(rule_id=rule.id, triggered=False)
        
        try:
            # Vérifier si la règle est active
            if not rule.enabled:
                return result
            
            # Vérifier cooldown
            if not rule.can_trigger():
                return result
            
            # Récupérer les valeurs des signaux
            signal_values = self.get_signal_values()
            result.condition_values = {
                sig: signal_values.get(sig)
                for sig in rule.required_signals
            }
            
            # Évaluer la condition
            triggered = rule.condition(signal_values)
            result.triggered = triggered
            
            if triggered:
                rule.mark_triggered()
                self._trigger_count += 1
                
                # Exécuter les actions
                for action in rule.actions:
                    await self._execute_action(rule.id, action)
                    result.actions_executed.append(action.action_type)
                
                # Notifier
                for callback in self._on_rule_triggered:
                    try:
                        if asyncio.iscoroutinefunction(callback):
                            await callback(result)
                        else:
                            callback(result)
                    except Exception as e:
                        logger.error("rule_triggered_callback_error", error=str(e))
                
                logger.info(
                    "rule_triggered",
                    rule_id=rule.id,
                    priority=rule.priority.name,
                    actions=result.actions_executed,
                )
        
        except Exception as e:
            result.error = str(e)
            self._error_count += 1
            logger.error(
                "rule_evaluation_error",
                rule_id=rule.id,
                error=str(e),
            )
        
        result.execution_time_ms = (datetime.now() - start_time).total_seconds() * 1000
        return result
    
    async def _execute_action(self, rule_id: str, action: RuleAction) -> None:
        """
        Exécute une action.
        
        Args:
            rule_id: ID de la règle source
            action: Action à exécuter
        """
        try:
            if action.action_type == ActionType.ESTOP:
                await self._state_machine.request_estop(
                    trigger=f"Rule {rule_id}",
                    rule_id=rule_id,
                )
            
            elif action.action_type == ActionType.STOP_CAT1:
                await self._state_machine.request_stop(
                    trigger=f"Rule {rule_id}",
                    rule_id=rule_id,
                )
            
            elif action.action_type == ActionType.SLOW_50:
                await self._state_machine.request_slow(
                    speed_percent=50,
                    trigger=f"Rule {rule_id}",
                    rule_id=rule_id,
                )
            
            elif action.action_type == ActionType.SLOW_25:
                await self._state_machine.request_slow(
                    speed_percent=25,
                    trigger=f"Rule {rule_id}",
                    rule_id=rule_id,
                )
            
            elif action.action_type == ActionType.ALERT:
                logger.warning(
                    "alert_triggered",
                    rule_id=rule_id,
                    target=action.target,
                    message=action.message,
                )
                # TODO: Envoyer alerte réelle (email, SMS, dashboard)
            
            elif action.action_type == ActionType.LOG:
                logger.info(
                    "rule_log",
                    rule_id=rule_id,
                    message=action.message,
                    data=action.data,
                )
            
            # Notifier callbacks
            for callback in self._on_action_executed:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(rule_id, action)
                    else:
                        callback(rule_id, action)
                except Exception as e:
                    logger.error("action_callback_error", error=str(e))
        
        except Exception as e:
            logger.error(
                "action_execution_error",
                rule_id=rule_id,
                action_type=action.action_type.value,
                error=str(e),
            )
    
    async def evaluate_all(self) -> List[RuleResult]:
        """
        Évalue toutes les règles par ordre de priorité.
        
        Returns:
            Liste des résultats
        """
        results = []
        self._eval_count += 1
        
        # Évaluer par priorité (P0 en premier)
        for priority in RulePriority:
            for rule in self._rules_by_priority[priority]:
                result = await self.evaluate_rule(rule)
                results.append(result)
                
                # Si P0 ou P1 déclenché, on peut court-circuiter
                if result.triggered and priority in (
                    RulePriority.P0_CRITICAL,
                    RulePriority.P1_HIGH,
                ):
                    # Les règles de haute priorité déclenchées
                    # peuvent bloquer les évaluations suivantes
                    pass
        
        # Historique
        self._results_history.extend(results)
        if len(self._results_history) > self._max_history:
            self._results_history = self._results_history[-self._max_history:]
        
        return results
    
    async def start(self) -> None:
        """Démarre l'évaluation continue des règles."""
        if self._running:
            return
        
        self._running = True
        self._eval_task = asyncio.create_task(self._evaluation_loop())
        logger.info("rule_engine_started")
    
    async def stop(self) -> None:
        """Arrête l'évaluation."""
        self._running = False
        if self._eval_task:
            self._eval_task.cancel()
            try:
                await self._eval_task
            except asyncio.CancelledError:
                pass
        logger.info("rule_engine_stopped")
    
    async def _evaluation_loop(self) -> None:
        """Boucle d'évaluation continue."""
        while self._running:
            await self.evaluate_all()
            await asyncio.sleep(self._evaluation_interval)
    
    def on_rule_triggered(self, callback: Callable[[RuleResult], None]) -> None:
        """Ajoute un callback pour les règles déclenchées."""
        self._on_rule_triggered.append(callback)
    
    def on_action_executed(
        self, 
        callback: Callable[[str, RuleAction], None]
    ) -> None:
        """Ajoute un callback pour les actions exécutées."""
        self._on_action_executed.append(callback)
    
    def get_stats(self) -> Dict:
        """Retourne les statistiques."""
        return {
            "total_rules": len(self._rules),
            "enabled_rules": sum(1 for r in self._rules.values() if r.enabled),
            "eval_count": self._eval_count,
            "trigger_count": self._trigger_count,
            "error_count": self._error_count,
            "rules_by_priority": {
                p.name: len(rules) 
                for p, rules in self._rules_by_priority.items()
            },
        }


# Règles pré-définies pour cellule soudage MIG
def get_welding_cell_rules() -> List[Rule]:
    """Retourne les règles pour une cellule de soudage MIG."""
    
    rules = [
        # === P0 - CRITIQUES ===
        Rule(
            id="RS-001",
            name="E-STOP pressed",
            priority=RulePriority.P0_CRITICAL,
            condition=lambda s: s.get("estop_status", 0) != 0,
            actions=[
                RuleAction(ActionType.ESTOP, message="E-STOP activated"),
                RuleAction(ActionType.LOG, data={"source": "estop_button"}),
            ],
            required_signals=["estop_status"],
            description="Arrêt urgence si bouton E-STOP pressé",
        ),
        
        Rule(
            id="RS-002",
            name="PLC heartbeat timeout",
            priority=RulePriority.P0_CRITICAL,
            condition=lambda s: s.get("plc_heartbeat") is None,
            actions=[
                RuleAction(ActionType.ESTOP, message="PLC communication lost"),
                RuleAction(ActionType.ALERT, target="HSE,MAINT"),
            ],
            required_signals=["plc_heartbeat"],
            description="E-STOP si perte communication PLC",
        ),
        
        Rule(
            id="RS-004",
            name="Arc ON + Door open",
            priority=RulePriority.P0_CRITICAL,
            condition=lambda s: (
                s.get("arc_on", False) and 
                s.get("door_status", "closed") == "open"
            ),
            actions=[
                RuleAction(ActionType.ESTOP, message="Arc active with door open"),
                RuleAction(ActionType.ALERT, target="OPERATOR"),
            ],
            required_signals=["arc_on", "door_status"],
            description="E-STOP si arc actif avec porte ouverte",
        ),
        
        # === P1 - ARRÊTS ===
        Rule(
            id="RS-010",
            name="Scanner PROTECT zone",
            priority=RulePriority.P1_HIGH,
            condition=lambda s: (s.get("scanner_zone_status", 0) & 0x04) != 0,  # Bit PROTECT
            actions=[
                RuleAction(ActionType.STOP_CAT1),
                RuleAction(ActionType.LOG, data={"zone": "PROTECT"}),
            ],
            required_signals=["scanner_zone_status"],
            description="STOP si intrusion zone PROTECT",
        ),
        
        Rule(
            id="RS-011",
            name="Vision distance critical AUTO",
            priority=RulePriority.P1_HIGH,
            condition=lambda s: (
                s.get("vision_presence", False) and
                s.get("vision_min_distance", 10000) < 800 and
                s.get("fanuc_mode") == "AUTO"
            ),
            actions=[
                RuleAction(ActionType.STOP_CAT1),
                RuleAction(ActionType.LOG, data={"trigger": "vision_distance"}),
            ],
            required_signals=["vision_presence", "vision_min_distance", "fanuc_mode"],
            description="STOP si personne <800mm en mode AUTO",
        ),
        
        Rule(
            id="RS-013",
            name="Fumes critical",
            priority=RulePriority.P1_HIGH,
            condition=lambda s: s.get("fumes_vlep_ratio", 0) > 1.2,
            actions=[
                RuleAction(ActionType.STOP_CAT1),
                RuleAction(ActionType.ALERT, target="OPERATOR,HSE", 
                          message="Fumées >120% VLEP"),
            ],
            required_signals=["fumes_vlep_ratio"],
            description="STOP si fumées >120% VLEP",
            cooldown_ms=5000,
        ),
        
        # === P2 - RALENTISSEMENTS ===
        Rule(
            id="RS-020",
            name="Scanner WARN zone",
            priority=RulePriority.P2_MEDIUM,
            condition=lambda s: (s.get("scanner_zone_status", 0) & 0x02) != 0,  # Bit WARN
            actions=[
                RuleAction(ActionType.SLOW_50),
            ],
            required_signals=["scanner_zone_status"],
            description="SLOW 50% si présence zone WARN",
        ),
        
        Rule(
            id="RS-021",
            name="Vision distance warning AUTO",
            priority=RulePriority.P2_MEDIUM,
            condition=lambda s: (
                s.get("vision_presence", False) and
                800 <= s.get("vision_min_distance", 10000) < 1500 and
                s.get("fanuc_mode") == "AUTO"
            ),
            actions=[
                RuleAction(ActionType.SLOW_50),
            ],
            required_signals=["vision_presence", "vision_min_distance", "fanuc_mode"],
            description="SLOW 50% si personne <1500mm en AUTO",
        ),
        
        Rule(
            id="RS-023",
            name="Fumes high",
            priority=RulePriority.P2_MEDIUM,
            condition=lambda s: 1.0 < s.get("fumes_vlep_ratio", 0) <= 1.2,
            actions=[
                RuleAction(ActionType.SLOW_25),
                RuleAction(ActionType.ALERT, target="OPERATOR", 
                          message="Fumées 100-120% VLEP"),
            ],
            required_signals=["fumes_vlep_ratio"],
            description="SLOW 25% + alerte si fumées 100-120% VLEP",
            cooldown_ms=10000,
        ),
        
        # === P3 - ALERTES ===
        Rule(
            id="RS-030",
            name="Fumes warning",
            priority=RulePriority.P3_LOW,
            condition=lambda s: 0.8 < s.get("fumes_vlep_ratio", 0) <= 1.0,
            actions=[
                RuleAction(ActionType.ALERT, target="OPERATOR", 
                          message="Fumées 80-100% VLEP"),
                RuleAction(ActionType.LOG, data={"type": "exposure_warning"}),
            ],
            required_signals=["fumes_vlep_ratio"],
            description="Alerte si fumées 80-100% VLEP",
            cooldown_ms=30000,
        ),
        
        Rule(
            id="RS-032",
            name="Arc exposure",
            priority=RulePriority.P3_LOW,
            condition=lambda s: (
                s.get("arc_on", False) and
                s.get("vision_presence", False) and
                s.get("fanuc_mode") == "AUTO"
            ),
            actions=[
                RuleAction(ActionType.ALERT, target="OPERATOR",
                          message="Exposition arc UV détectée"),
                RuleAction(ActionType.LOG, data={"type": "arc_exposure"}),
            ],
            required_signals=["arc_on", "vision_presence", "fanuc_mode"],
            description="Alerte si présence pendant arc en AUTO",
            cooldown_ms=60000,
        ),
        
        Rule(
            id="RS-035",
            name="Override excessive",
            priority=RulePriority.P3_LOW,
            condition=lambda s: s.get("override_count_week", 0) > 3,
            actions=[
                RuleAction(ActionType.ALERT, target="HSE",
                          message="Overrides sécurité >3/semaine"),
            ],
            required_signals=["override_count_week"],
            description="Alerte HSE si trop d'overrides",
            cooldown_ms=86400000,  # 24h
        ),
        
        # === P4 - DIAGNOSTIC ===
        Rule(
            id="RS-040",
            name="Camera fault",
            priority=RulePriority.P4_DIAGNOSTIC,
            condition=lambda s: s.get("camera_status") == "fault",
            actions=[
                RuleAction(ActionType.SET_DEGRADED, data={"system": "vision"}),
                RuleAction(ActionType.ALERT, target="MAINT"),
                RuleAction(ActionType.INCREASE_MARGIN, data={"percent": 30}),
            ],
            required_signals=["camera_status"],
            description="Mode dégradé si caméra en défaut",
        ),
        
        Rule(
            id="RS-041",
            name="Fumes sensor fault",
            priority=RulePriority.P4_DIAGNOSTIC,
            condition=lambda s: s.get("fumes_sensor_status") == "fault",
            actions=[
                RuleAction(ActionType.SET_DEGRADED, data={"system": "fumes"}),
                RuleAction(ActionType.ALERT, target="HSE,MAINT"),
            ],
            required_signals=["fumes_sensor_status"],
            description="Mode dégradé si capteur fumées en défaut",
        ),
    ]
    
    return rules
