"""
RoboSafe Sentinel - Script d'intégration principal.

Connecte tous les composants du système:
- Drivers capteurs (ou simulateurs)
- Agents AgenticX5
- Machine d'états
- Moteur de règles
- API REST + WebSocket

Usage:
    # Mode simulation (sans matériel)
    python -m robosafe.integration --simulate
    
    # Mode production (avec matériel réel)
    python -m robosafe.integration --config config/production.yaml
    
    # Avec API sur port spécifique
    python -m robosafe.integration --simulate --port 8080
"""

import asyncio
import argparse
import signal
import sys
from datetime import datetime
from typing import Optional
import structlog

# Core
from robosafe.core.state_machine import SafetyStateMachine, SafetyState
from robosafe.core.signal_manager import SignalManager, SignalSource
from robosafe.core.rule_engine import RuleEngine, Rule, RulePriority, RuleAction

# Sensors
from robosafe.sensors import (
    SiemensS7Simulator,
    FanucSimulator,
    SICKScannerSimulator,
    FumesSensorSimulator,
    VisionSimulator,
)

# Agents
from robosafe.agents import (
    PerceptionAgent,
    AnalysisAgent,
    DecisionAgent,
    OrchestratorAgent,
    AgentMessage,
    ActionType,
)

# API
from robosafe.api.server import app, init_api
from robosafe.api.websocket_manager import WebSocketManager

logger = structlog.get_logger(__name__)


class RoboSafeSentinel:
    """
    Système intégré RoboSafe Sentinel.
    
    Orchestre tous les composants pour la supervision
    de sécurité d'une cellule robotisée.
    """
    
    def __init__(
        self,
        simulate: bool = True,
        cell_id: str = "WELD-MIG-001",
    ):
        """
        Initialise le système.
        
        Args:
            simulate: Utiliser les simulateurs au lieu du matériel réel
            cell_id: Identifiant de la cellule
        """
        self.simulate = simulate
        self.cell_id = cell_id
        self._running = False
        
        # === Core Components ===
        self.state_machine = SafetyStateMachine()
        self.signal_manager = SignalManager()
        self.rule_engine = RuleEngine(self.signal_manager, self.state_machine)
        
        # === Sensors/Simulators ===
        self.plc_driver = None
        self.robot_driver = None
        self.scanner_driver = None
        self.fumes_driver = None
        self.vision_driver = None
        
        # === Agents ===
        self.perception_agent = PerceptionAgent()
        self.analysis_agent = AnalysisAgent()
        self.decision_agent = DecisionAgent()
        self.orchestrator_agent = OrchestratorAgent()
        
        # === WebSocket ===
        self.ws_manager = WebSocketManager()
        
        logger.info(
            "sentinel_initialized",
            cell_id=cell_id,
            simulate=simulate,
        )
    
    async def start(self) -> None:
        """Démarre tous les composants."""
        logger.info("sentinel_starting", cell_id=self.cell_id)
        
        # 1. Initialiser les drivers/simulateurs
        await self._init_sensors()
        
        # 2. Configurer les règles de sécurité
        self._setup_safety_rules()
        
        # 3. Connecter les agents entre eux
        self._wire_agents()
        
        # 4. Enregistrer les exécuteurs d'actions
        self._register_action_executors()
        
        # 5. Démarrer les agents
        await self.perception_agent.start()
        await self.analysis_agent.start()
        await self.decision_agent.start()
        await self.orchestrator_agent.start()
        
        # 6. Initialiser l'API
        init_api(self.signal_manager, self.state_machine, self.rule_engine)
        
        self._running = True
        
        # 7. Lancer la boucle principale
        await self._main_loop()
    
    async def stop(self) -> None:
        """Arrête proprement tous les composants."""
        logger.info("sentinel_stopping")
        self._running = False
        
        # Arrêter les agents
        await self.orchestrator_agent.stop()
        await self.decision_agent.stop()
        await self.analysis_agent.stop()
        await self.perception_agent.stop()
        
        # Déconnecter les drivers
        if self.plc_driver:
            await self.plc_driver.disconnect()
        if self.robot_driver:
            await self.robot_driver.disconnect()
        if self.scanner_driver:
            await self.scanner_driver.disconnect()
        if self.fumes_driver:
            await self.fumes_driver.disconnect()
        if self.vision_driver:
            await self.vision_driver.disconnect()
        
        # Fermer WebSocket
        await self.ws_manager.disconnect_all()
        
        logger.info("sentinel_stopped")
    
    async def _init_sensors(self) -> None:
        """Initialise les capteurs ou simulateurs."""
        if self.simulate:
            logger.info("initializing_simulators")
            
            # PLC Siemens
            self.plc_driver = SiemensS7Simulator()
            await self.plc_driver.connect()
            
            # Robot Fanuc
            self.robot_driver = FanucSimulator()
            await self.robot_driver.connect()
            
            # Scanner SICK
            self.scanner_driver = SICKScannerSimulator()
            await self.scanner_driver.connect()
            
            # Fumées
            self.fumes_driver = FumesSensorSimulator()
            await self.fumes_driver.connect()
            
            # Vision IA
            self.vision_driver = VisionSimulator()
            await self.vision_driver.connect()
            
            # Démarrer les simulations
            await self.plc_driver.start_cyclic_read()
            await self.robot_driver.start_cyclic_read()
            await self.scanner_driver.start_cyclic_read()
            await self.fumes_driver.start_cyclic_read()
            await self.vision_driver.start_processing()
            
        else:
            logger.info("initializing_real_hardware")
            # TODO: Initialiser les vrais drivers avec config
            raise NotImplementedError("Real hardware not yet implemented")
        
        # Ajouter les callbacks au PerceptionAgent
        self.perception_agent.add_sensor_callback(self._collect_all_sensors)
    
    def _collect_all_sensors(self) -> dict:
        """Collecte les données de tous les capteurs."""
        signals = {}
        
        # PLC
        if self.plc_driver and self.plc_driver.is_connected:
            status = self.plc_driver.current_status
            if status:
                signals["plc_heartbeat"] = status.plc_heartbeat
                signals["estop_status"] = 0 if status.estop_active else 1
                signals["door_closed"] = status.door_closed
                signals["plc_safety_ok"] = status.safety_ok
        
        # Robot
        if self.robot_driver and self.robot_driver.is_connected:
            status = self.robot_driver.current_status
            if status:
                signals["fanuc_tcp_speed"] = status.current_speed_mms
                signals["fanuc_mode"] = status.mode.name
                signals["fanuc_speed_override"] = status.speed_override
                signals["fanuc_running"] = status.in_motion
        
        # Scanner
        if self.scanner_driver and self.scanner_driver.is_connected:
            measurement = self.scanner_driver.current_measurement
            if measurement:
                signals["scanner_min_distance"] = measurement.min_distance_mm
                signals["scanner_zone_status"] = measurement.active_zone.value
                signals["scanner_contamination"] = measurement.contamination_level
        
        # Fumées
        if self.fumes_driver and self.fumes_driver.is_connected:
            measurement = self.fumes_driver.current_measurement
            if measurement:
                signals["fumes_concentration"] = measurement.concentration_mgm3
                signals["fumes_vlep_ratio"] = measurement.vlep_ratio
                signals["fumes_alert_level"] = measurement.alert_level.value
        
        # Vision
        if self.vision_driver and self.vision_driver.is_connected:
            result = self.vision_driver.current_result
            if result:
                signals["vision_person_count"] = result.persons_detected
                signals["vision_min_distance"] = result.min_distance_mm if result.min_distance_mm != float('inf') else 10000
                signals["vision_ppe_ok"] = result.all_ppe_ok
                signals["vision_intrusion"] = result.intrusion_detected
        
        return signals
    
    def _setup_safety_rules(self) -> None:
        """Configure les règles de sécurité."""
        # RS-001: Distance critique
        self.rule_engine.register_rule(Rule(
            id="RS-001",
            name="Distance critique",
            priority=RulePriority.P0_CRITICAL,
            condition=lambda ctx: ctx.get("scanner_min_distance", 10000) < 500,
            actions=[RuleAction(action_type="ESTOP", message="Distance < 500mm")],
        ))
        
        # RS-002: Distance warning
        self.rule_engine.register_rule(Rule(
            id="RS-002",
            name="Distance warning",
            priority=RulePriority.P1_HIGH,
            condition=lambda ctx: 500 <= ctx.get("scanner_min_distance", 10000) < 800,
            actions=[RuleAction(action_type="SLOW_25", message="Distance 500-800mm")],
        ))
        
        # RS-003: Distance monitoring
        self.rule_engine.register_rule(Rule(
            id="RS-003",
            name="Distance monitoring",
            priority=RulePriority.P2_MEDIUM,
            condition=lambda ctx: 800 <= ctx.get("scanner_min_distance", 10000) < 1200,
            actions=[RuleAction(action_type="SLOW_50", message="Distance 800-1200mm")],
        ))
        
        # RS-004: Fumées critiques
        self.rule_engine.register_rule(Rule(
            id="RS-004",
            name="Fumées critiques",
            priority=RulePriority.P0_CRITICAL,
            condition=lambda ctx: ctx.get("fumes_vlep_ratio", 0) >= 1.2,
            actions=[RuleAction(action_type="STOP", message="Fumées > 120% VLEP")],
        ))
        
        # RS-005: Fumées élevées
        self.rule_engine.register_rule(Rule(
            id="RS-005",
            name="Fumées élevées",
            priority=RulePriority.P2_MEDIUM,
            condition=lambda ctx: 0.8 <= ctx.get("fumes_vlep_ratio", 0) < 1.2,
            actions=[RuleAction(action_type="ALERT", message="Fumées 80-120% VLEP")],
        ))
        
        # RS-006: Intrusion vision
        self.rule_engine.register_rule(Rule(
            id="RS-006",
            name="Intrusion zone danger",
            priority=RulePriority.P0_CRITICAL,
            condition=lambda ctx: ctx.get("vision_intrusion", False),
            actions=[RuleAction(action_type="ESTOP", message="Intrusion détectée")],
        ))
        
        # RS-007: EPI manquant
        self.rule_engine.register_rule(Rule(
            id="RS-007",
            name="EPI manquant",
            priority=RulePriority.P2_MEDIUM,
            condition=lambda ctx: ctx.get("vision_person_count", 0) > 0 and not ctx.get("vision_ppe_ok", True),
            actions=[RuleAction(action_type="ALERT", message="EPI non détecté")],
        ))
        
        # RS-008: E-STOP physique
        self.rule_engine.register_rule(Rule(
            id="RS-008",
            name="E-STOP physique",
            priority=RulePriority.P0_CRITICAL,
            condition=lambda ctx: ctx.get("estop_status", 0) == 1,
            actions=[RuleAction(action_type="ESTOP", message="Bouton E-STOP activé")],
        ))
        
        logger.info("safety_rules_registered", count=8)
    
    def _wire_agents(self) -> None:
        """Connecte les agents entre eux."""
        
        def route_message(msg: AgentMessage):
            """Route les messages entre agents."""
            target = msg.target
            
            # Broadcast ou ciblé
            if target == "" or target == "analysis":
                asyncio.create_task(self.analysis_agent.receive(msg))
            if target == "" or target == "decision":
                asyncio.create_task(self.decision_agent.receive(msg))
            if target == "" or target == "orchestrator":
                asyncio.create_task(self.orchestrator_agent.receive(msg))
            if target == "" or target == "perception":
                asyncio.create_task(self.perception_agent.receive(msg))
            
            # Broadcast WebSocket pour le dashboard
            if msg.type in ["risk_update", "execution_result", "system_state"]:
                asyncio.create_task(self.ws_manager.broadcast({
                    "type": msg.type,
                    "payload": msg.payload,
                    "timestamp": datetime.now().isoformat(),
                }))
        
        self.perception_agent.set_outbox_callback(route_message)
        self.analysis_agent.set_outbox_callback(route_message)
        self.decision_agent.set_outbox_callback(route_message)
        self.orchestrator_agent.set_outbox_callback(route_message)
        
        logger.info("agents_wired")
    
    def _register_action_executors(self) -> None:
        """Enregistre les exécuteurs d'actions."""
        
        async def execute_estop(rec: dict) -> bool:
            """Exécute un E-STOP."""
            logger.warning("executing_estop", reason=rec.get("reason"))
            await self.state_machine.transition_to(
                SafetyState.ESTOP,
                trigger=rec.get("reason", "Agent decision"),
            )
            # Commander le PLC
            if self.plc_driver and hasattr(self.plc_driver, 'send_command'):
                await self.plc_driver.send_command("ESTOP")
            return True
        
        async def execute_stop(rec: dict) -> bool:
            """Exécute un STOP."""
            logger.warning("executing_stop", reason=rec.get("reason"))
            await self.state_machine.transition_to(
                SafetyState.STOP,
                trigger=rec.get("reason", "Agent decision"),
            )
            if self.plc_driver and hasattr(self.plc_driver, 'send_command'):
                await self.plc_driver.send_command("STOP_CAT1")
            return True
        
        async def execute_slow_50(rec: dict) -> bool:
            """Exécute un ralentissement 50%."""
            logger.info("executing_slow_50", reason=rec.get("reason"))
            await self.state_machine.transition_to(
                SafetyState.SLOW_50,
                trigger=rec.get("reason", "Agent decision"),
            )
            if self.plc_driver and hasattr(self.plc_driver, 'send_command'):
                await self.plc_driver.send_command("SLOW_50")
            return True
        
        async def execute_slow_25(rec: dict) -> bool:
            """Exécute un ralentissement 25%."""
            logger.info("executing_slow_25", reason=rec.get("reason"))
            await self.state_machine.transition_to(
                SafetyState.SLOW_25,
                trigger=rec.get("reason", "Agent decision"),
            )
            if self.plc_driver and hasattr(self.plc_driver, 'send_command'):
                await self.plc_driver.send_command("SLOW_25")
            return True
        
        async def execute_alert(rec: dict) -> bool:
            """Envoie une alerte."""
            logger.info("executing_alert", reason=rec.get("reason"))
            # Broadcast via WebSocket
            await self.ws_manager.broadcast({
                "type": "alert",
                "level": "WARNING",
                "message": rec.get("reason"),
                "timestamp": datetime.now().isoformat(),
            })
            return True
        
        # Enregistrer dans l'orchestrateur
        self.orchestrator_agent.register_executor("ESTOP", execute_estop)
        self.orchestrator_agent.register_executor("STOP", execute_stop)
        self.orchestrator_agent.register_executor("SLOW_50", execute_slow_50)
        self.orchestrator_agent.register_executor("SLOW_25", execute_slow_25)
        self.orchestrator_agent.register_executor("ALERT", execute_alert)
        
        logger.info("action_executors_registered")
    
    async def _main_loop(self) -> None:
        """Boucle principale du système."""
        logger.info("main_loop_started")
        
        cycle_count = 0
        
        while self._running:
            try:
                # 1. Collecter les signaux et mettre à jour le SignalManager
                signals = self._collect_all_sensors()
                for sig_id, value in signals.items():
                    await self.signal_manager.update_signal(
                        signal_id=sig_id,
                        value=value,
                        
                    )
                
                # 2. Évaluer les règles de sécurité
                triggered_rules: Any = await self.rule_engine.evaluate_all()
                
                # 3. Appliquer les actions des règles (en plus des agents)
                for result in triggered_rules:
                    if result.triggered and result.actions_executed:
                        for action in result.actions_executed:
                            action_type = action.get("action_type", "") if isinstance(action, dict) else getattr(action, "action_type", "")
                            if action_type == "ESTOP":
                                await self.state_machine.transition_to(
                                    SafetyState.ESTOP,
                                    trigger=f"Rule {result.rule_id}",
                                )
                            elif action_type == "STOP":
                                await self.state_machine.transition_to(
                                    SafetyState.STOP,
                                    trigger=f"Rule {result.rule_id}",
                                )
                
                # 4. Broadcast état périodique (toutes les 10 itérations)
                cycle_count += 1
                if cycle_count % 10 == 0:
                    await self._broadcast_status()
                
            except Exception as e:
                logger.error("main_loop_error", error=str(e))
            
            await asyncio.sleep(0.05)  # 20 Hz
    
    async def _broadcast_status(self) -> None:
        """Broadcast l'état du système via WebSocket."""
        status = {
            "type": "status",
            "timestamp": datetime.now().isoformat(),
            "state": self.state_machine.get_status(),
            "signals": {
                sig_id: {
                    "value": sig.value,
                    "quality": sig.quality.value,
                }
                for sig_id, sig in self.signal_manager.get_all_signals().items()
            },
            "agents": {
                "perception": self.perception_agent.metrics,
                "analysis": self.analysis_agent.metrics,
                "decision": self.decision_agent.metrics,
                "orchestrator": self.orchestrator_agent.metrics,
            },
        }
        
        await self.ws_manager.broadcast(status)


async def run_with_api(sentinel: RoboSafeSentinel, host: str, port: int):
    """Lance le système avec l'API."""
    import uvicorn
    
    # Configurer uvicorn
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info",
    )
    server = uvicorn.Server(config)
    
    # Lancer en parallèle
    await asyncio.gather(
        sentinel.start(),
        server.serve(),
    )


def main():
    """Point d'entrée principal."""
    parser = argparse.ArgumentParser(
        description="RoboSafe Sentinel - Système de supervision sécurité"
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Utiliser les simulateurs au lieu du matériel réel",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Fichier de configuration YAML",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Adresse IP du serveur API",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port du serveur API",
    )
    parser.add_argument(
        "--cell-id",
        type=str,
        default="WELD-MIG-001",
        help="Identifiant de la cellule",
    )
    
    args = parser.parse_args()
    
    # Créer le système
    sentinel = RoboSafeSentinel(
        simulate=args.simulate or args.config is None,
        cell_id=args.cell_id,
    )
    
    # Gestion des signaux d'arrêt
    def signal_handler(sig, frame):
        print("\n🛑 Arrêt demandé...")
        asyncio.create_task(sentinel.stop())
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Lancer
    print(f"""
╔══════════════════════════════════════════════════════════════╗
║           🤖 RoboSafe Sentinel - Démarrage                   ║
╠══════════════════════════════════════════════════════════════╣
║  Cellule:    {args.cell_id:<46} ║
║  Mode:       {"Simulation" if args.simulate else "Production":<46} ║
║  API:        http://{args.host}:{args.port:<36} ║
║  Dashboard:  http://{args.host}:{args.port}/static/dashboard.html{" "*6} ║
╚══════════════════════════════════════════════════════════════╝
    """)
    
    try:
        asyncio.run(run_with_api(sentinel, args.host, args.port))
    except KeyboardInterrupt:
        print("\n👋 Au revoir!")


if __name__ == "__main__":
    main()
