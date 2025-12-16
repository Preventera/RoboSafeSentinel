"""
Point d'entrée principal RoboSafe Sentinel.

Usage:
    python -m robosafe.main --config config/config.yaml
    python -m robosafe.main --mode simulation --verbose
"""

import asyncio
import signal
import sys
from pathlib import Path
from typing import Optional

import click
import structlog
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from robosafe import __version__
from robosafe.core.state_machine import SafetyStateMachine, SafetyState
from robosafe.core.signal_manager import SignalManager, get_welding_cell_signals
from robosafe.core.rule_engine import RuleEngine, get_welding_cell_rules
from robosafe.utils.config import load_config, RoboSafeConfig
from robosafe.utils.logger import setup_logging

console = Console()
logger = structlog.get_logger(__name__)


class RoboSafeSentinel:
    """
    Application principale RoboSafe Sentinel.
    
    Orchestre tous les composants:
    - Signal Manager (collecte)
    - State Machine (états sécurité)
    - Rule Engine (évaluation règles)
    - API Server (interface externe)
    """
    
    def __init__(
        self,
        config: Optional[RoboSafeConfig] = None,
        simulation_mode: bool = False,
    ):
        """
        Initialise RoboSafe.
        
        Args:
            config: Configuration (ou défaut si None)
            simulation_mode: Mode simulation sans équipements
        """
        self.config = config or RoboSafeConfig()
        self.simulation_mode = simulation_mode
        
        # Composants
        self.signal_manager = SignalManager()
        self.state_machine = SafetyStateMachine(
            on_transition=self._on_state_transition
        )
        self.rule_engine = RuleEngine(
            signal_manager=self.signal_manager,
            state_machine=self.state_machine,
        )
        
        # État
        self._running = False
        self._shutdown_event = asyncio.Event()
        
        logger.info(
            "robosafe_sentinel_initialized",
            version=__version__,
            simulation_mode=simulation_mode,
            cell_id=self.config.cell.id if self.config.cell else "default",
        )
    
    def _on_state_transition(self, transition) -> None:
        """Callback pour les transitions d'état."""
        console.print(
            f"[bold]État: {transition.from_state.name} → {transition.to_state.name}[/bold]",
            style="yellow" if transition.to_state != SafetyState.NORMAL else "green",
        )
    
    async def setup(self) -> None:
        """Configure tous les composants."""
        logger.info("setting_up_robosafe")
        
        # Enregistrer les signaux
        signals = get_welding_cell_signals()
        self.signal_manager.register_signals(signals)
        logger.info("signals_registered", count=len(signals))
        
        # Enregistrer les règles
        rules = get_welding_cell_rules()
        self.rule_engine.register_rules(rules)
        logger.info("rules_registered", count=len(rules))
        
        # Callbacks
        self.rule_engine.on_rule_triggered(self._on_rule_triggered)
        
        # TODO: Initialiser les drivers capteurs selon config
        if not self.simulation_mode:
            await self._setup_drivers()
    
    async def _setup_drivers(self) -> None:
        """Configure les drivers de communication."""
        # TODO: Implémenter connexions réelles
        # - Fanuc EtherNet/IP
        # - Siemens S7 PROFIsafe
        # - SICK scanners
        # - Vision IA
        # - Capteur fumées
        logger.info("drivers_setup_skipped", reason="not_implemented")
    
    def _on_rule_triggered(self, result) -> None:
        """Callback pour les règles déclenchées."""
        style = "red" if "ESTOP" in str(result.actions_executed) else "yellow"
        console.print(
            f"[{style}]Règle {result.rule_id} déclenchée: {result.actions_executed}[/{style}]"
        )
    
    async def start(self) -> None:
        """Démarre RoboSafe."""
        logger.info("starting_robosafe")
        self._running = True
        
        # Démarrer les composants
        await self.signal_manager.start_watchdog()
        await self.rule_engine.start()
        
        # Passer en état NORMAL après init
        await self.state_machine.transition_to(
            SafetyState.NORMAL,
            trigger="initialization_complete",
        )
        
        self._print_status()
        
        # Si simulation, démarrer le générateur de données
        if self.simulation_mode:
            asyncio.create_task(self._simulation_loop())
        
        logger.info("robosafe_started")
    
    async def stop(self) -> None:
        """Arrête RoboSafe."""
        logger.info("stopping_robosafe")
        self._running = False
        
        await self.rule_engine.stop()
        await self.signal_manager.stop_watchdog()
        
        logger.info("robosafe_stopped")
    
    async def run(self) -> None:
        """Boucle principale."""
        await self.setup()
        await self.start()
        
        try:
            while self._running and not self._shutdown_event.is_set():
                await asyncio.sleep(1)
                
                # Afficher stats périodiquement
                if self.rule_engine._eval_count % 100 == 0:
                    self._print_status()
        
        except asyncio.CancelledError:
            logger.info("run_cancelled")
        finally:
            await self.stop()
    
    async def _simulation_loop(self) -> None:
        """Génère des données de simulation."""
        import random
        
        logger.info("simulation_loop_started")
        
        while self._running:
            # Simuler des valeurs de signaux
            await self.signal_manager.update_signals_batch({
                "fanuc_tcp_speed": random.uniform(0, 500),
                "fanuc_mode": random.choice(["AUTO", "T1", "T2"]),
                "fanuc_servo_on": True,
                "plc_heartbeat": random.randint(0, 65535),
                "scanner_zone_status": random.choice([0x00, 0x01, 0x02, 0x04]),
                "scanner_min_distance": random.randint(500, 3000),
                "estop_status": 0,
                "arc_on": random.choice([True, False]),
                "fumes_concentration": random.uniform(0, 8),
                "fumes_vlep_ratio": random.uniform(0.2, 1.5),
                "vision_presence": random.choice([True, False, False]),
                "vision_min_distance": random.randint(800, 5000),
                "vision_confidence": random.uniform(0.7, 0.99),
                "robosafe_risk_score": random.uniform(10, 70),
            })
            
            await asyncio.sleep(0.1)
    
    def _print_status(self) -> None:
        """Affiche le statut actuel."""
        state = self.state_machine.current_state
        
        table = Table(title="RoboSafe Sentinel Status")
        table.add_column("Composant", style="cyan")
        table.add_column("Valeur", style="green")
        
        table.add_row("Version", __version__)
        table.add_row("Mode", "Simulation" if self.simulation_mode else "Production")
        table.add_row("État", state.name)
        table.add_row("Vitesse max", f"{state.max_speed_percent}%")
        table.add_row("Évaluations", str(self.rule_engine._eval_count))
        table.add_row("Déclenchements", str(self.rule_engine._trigger_count))
        table.add_row("Signaux", str(len(self.signal_manager._signals)))
        
        console.print(table)
    
    def shutdown(self) -> None:
        """Demande l'arrêt."""
        self._shutdown_event.set()


def handle_signals(sentinel: RoboSafeSentinel) -> None:
    """Configure les handlers de signaux système."""
    
    def _handler(signum, frame):
        console.print("\n[yellow]Arrêt demandé...[/yellow]")
        sentinel.shutdown()
    
    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)


@click.command()
@click.option(
    "--config", "-c",
    type=click.Path(exists=True, path_type=Path),
    help="Fichier de configuration YAML",
)
@click.option(
    "--mode", "-m",
    type=click.Choice(["production", "simulation"]),
    default="production",
    help="Mode d'exécution",
)
@click.option(
    "--verbose", "-v",
    is_flag=True,
    help="Mode verbeux",
)
@click.option(
    "--version",
    is_flag=True,
    help="Affiche la version",
)
def main(
    config: Optional[Path],
    mode: str,
    verbose: bool,
    version: bool,
) -> None:
    """
    RoboSafe Sentinel - Système de sécurité intelligent.
    
    Démarre le système de supervision de sécurité pour cellules robotisées.
    """
    if version:
        console.print(f"RoboSafe Sentinel v{__version__}")
        return
    
    # Setup logging
    log_level = "DEBUG" if verbose else "INFO"
    setup_logging(level=log_level)
    
    # Bannière
    console.print(Panel.fit(
        f"[bold blue]🤖 RoboSafe Sentinel v{__version__}[/bold blue]\n"
        f"[dim]Système de sécurité intelligent AgenticX5[/dim]",
        border_style="blue",
    ))
    
    # Charger configuration
    cfg = None
    if config:
        cfg = load_config(config)
        console.print(f"[green]Configuration chargée: {config}[/green]")
    
    # Créer l'application
    simulation_mode = mode == "simulation"
    sentinel = RoboSafeSentinel(config=cfg, simulation_mode=simulation_mode)
    
    # Handlers signaux
    handle_signals(sentinel)
    
    # Lancer
    try:
        asyncio.run(sentinel.run())
    except KeyboardInterrupt:
        pass
    finally:
        console.print("[green]RoboSafe arrêté.[/green]")


if __name__ == "__main__":
    main()
