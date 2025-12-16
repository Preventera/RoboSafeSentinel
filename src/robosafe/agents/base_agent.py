"""
Agent de base pour l'architecture AgenticX5.

Définit l'interface commune pour tous les agents du système.
Chaque agent hérite de cette classe et implémente sa logique spécifique.

Architecture AgenticX5 - 5 niveaux:
    Level 1: Collecte (PerceptionAgent)
    Level 2: Normalisation (PerceptionAgent)
    Level 3: Analyse (AnalysisAgent)
    Level 4: Recommandation (DecisionAgent)
    Level 5: Orchestration (OrchestratorAgent)
"""

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from typing import Any, Callable, Dict, List, Optional, Set
from uuid import uuid4
import structlog

logger = structlog.get_logger(__name__)


class AgentLevel(IntEnum):
    """Niveaux d'agents dans l'architecture AgenticX5."""
    COLLECT = 1       # Collecte brute des données
    NORMALIZE = 2     # Normalisation et validation
    ANALYZE = 3       # Analyse et détection patterns
    RECOMMEND = 4     # Recommandation d'actions
    ORCHESTRATE = 5   # Orchestration et arbitrage


class AgentState(IntEnum):
    """États possibles d'un agent."""
    INIT = 0
    RUNNING = 1
    PAUSED = 2
    STOPPED = 3
    ERROR = 4


class MessagePriority(IntEnum):
    """Priorité des messages inter-agents."""
    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3


@dataclass
class AgentMessage:
    """Message échangé entre agents."""
    id: str = field(default_factory=lambda: str(uuid4())[:8])
    source: str = ""
    target: str = ""  # Vide = broadcast
    type: str = ""
    priority: MessagePriority = MessagePriority.NORMAL
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)
    requires_ack: bool = False
    ttl_seconds: float = 10.0
    
    @property
    def is_expired(self) -> bool:
        """Vérifie si le message a expiré."""
        age = (datetime.now() - self.timestamp).total_seconds()
        return age > self.ttl_seconds


@dataclass
class AgentConfig:
    """Configuration de base d'un agent."""
    name: str = "agent"
    level: AgentLevel = AgentLevel.COLLECT
    enabled: bool = True
    cycle_time_ms: int = 100
    max_queue_size: int = 1000
    log_level: str = "INFO"


class BaseAgent(ABC):
    """
    Classe de base pour tous les agents AgenticX5.
    
    Fonctionnalités communes:
    - Cycle de vie (start, stop, pause)
    - File de messages entrante
    - Communication avec autres agents
    - Métriques et logging
    """
    
    def __init__(self, config: AgentConfig):
        """
        Initialise l'agent.
        
        Args:
            config: Configuration de l'agent
        """
        self.config = config
        self.id = f"{config.name}-{str(uuid4())[:6]}"
        self._state = AgentState.INIT
        self._running = False
        self._task: Optional[asyncio.Task] = None
        
        # File de messages
        self._inbox: asyncio.Queue[AgentMessage] = asyncio.Queue(
            maxsize=config.max_queue_size
        )
        
        # Callbacks pour envoi de messages
        self._message_handlers: Dict[str, Callable] = {}
        self._outbox_callback: Optional[Callable[[AgentMessage], None]] = None
        
        # Métriques
        self._metrics = {
            "messages_received": 0,
            "messages_sent": 0,
            "messages_dropped": 0,
            "cycles_executed": 0,
            "errors": 0,
            "last_cycle_ms": 0.0,
            "avg_cycle_ms": 0.0,
        }
        
        # Timestamp
        self._started_at: Optional[datetime] = None
        self._last_cycle_at: Optional[datetime] = None
        
        logger.info(
            "agent_initialized",
            agent_id=self.id,
            name=config.name,
            level=config.level.name,
        )
    
    @property
    def name(self) -> str:
        """Nom de l'agent."""
        return self.config.name
    
    @property
    def level(self) -> AgentLevel:
        """Niveau de l'agent."""
        return self.config.level
    
    @property
    def state(self) -> AgentState:
        """État actuel de l'agent."""
        return self._state
    
    @property
    def is_running(self) -> bool:
        """Indique si l'agent est en cours d'exécution."""
        return self._state == AgentState.RUNNING
    
    @property
    def metrics(self) -> Dict[str, Any]:
        """Métriques de l'agent."""
        return {
            **self._metrics,
            "state": self._state.name,
            "uptime_seconds": self._get_uptime(),
            "inbox_size": self._inbox.qsize(),
        }
    
    def _get_uptime(self) -> float:
        """Calcule le temps de fonctionnement."""
        if self._started_at:
            return (datetime.now() - self._started_at).total_seconds()
        return 0.0
    
    async def start(self) -> None:
        """Démarre l'agent."""
        if self._running:
            return
        
        self._running = True
        self._state = AgentState.RUNNING
        self._started_at = datetime.now()
        
        # Initialisation spécifique
        await self.on_start()
        
        # Lancer la boucle principale
        self._task = asyncio.create_task(self._main_loop())
        
        logger.info("agent_started", agent_id=self.id)
    
    async def stop(self) -> None:
        """Arrête l'agent."""
        self._running = False
        self._state = AgentState.STOPPED
        
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        
        await self.on_stop()
        
        logger.info("agent_stopped", agent_id=self.id)
    
    async def pause(self) -> None:
        """Met l'agent en pause."""
        self._state = AgentState.PAUSED
        logger.info("agent_paused", agent_id=self.id)
    
    async def resume(self) -> None:
        """Reprend l'exécution de l'agent."""
        if self._state == AgentState.PAUSED:
            self._state = AgentState.RUNNING
            logger.info("agent_resumed", agent_id=self.id)
    
    async def _main_loop(self) -> None:
        """Boucle principale de l'agent."""
        cycle_time = self.config.cycle_time_ms / 1000.0
        
        while self._running:
            if self._state == AgentState.PAUSED:
                await asyncio.sleep(0.1)
                continue
            
            cycle_start = datetime.now()
            
            try:
                # Traiter les messages entrants
                await self._process_inbox()
                
                # Exécuter le cycle de l'agent
                await self.cycle()
                
                self._metrics["cycles_executed"] += 1
                
            except Exception as e:
                self._metrics["errors"] += 1
                logger.error(
                    "agent_cycle_error",
                    agent_id=self.id,
                    error=str(e),
                )
            
            # Calculer le temps de cycle
            cycle_duration = (datetime.now() - cycle_start).total_seconds() * 1000
            self._metrics["last_cycle_ms"] = cycle_duration
            self._update_avg_cycle(cycle_duration)
            self._last_cycle_at = datetime.now()
            
            # Attendre le prochain cycle
            sleep_time = max(0, cycle_time - cycle_duration / 1000)
            await asyncio.sleep(sleep_time)
    
    def _update_avg_cycle(self, new_value: float) -> None:
        """Met à jour la moyenne mobile du temps de cycle."""
        alpha = 0.1  # Facteur de lissage
        current = self._metrics["avg_cycle_ms"]
        self._metrics["avg_cycle_ms"] = alpha * new_value + (1 - alpha) * current
    
    async def _process_inbox(self) -> None:
        """Traite les messages en attente."""
        processed = 0
        max_per_cycle = 10  # Limite par cycle
        
        while not self._inbox.empty() and processed < max_per_cycle:
            try:
                message = self._inbox.get_nowait()
                
                # Vérifier expiration
                if message.is_expired:
                    self._metrics["messages_dropped"] += 1
                    continue
                
                await self.handle_message(message)
                self._metrics["messages_received"] += 1
                processed += 1
                
            except asyncio.QueueEmpty:
                break
    
    async def receive(self, message: AgentMessage) -> bool:
        """
        Reçoit un message dans la file d'attente.
        
        Args:
            message: Message à recevoir
            
        Returns:
            True si le message a été ajouté
        """
        try:
            self._inbox.put_nowait(message)
            return True
        except asyncio.QueueFull:
            self._metrics["messages_dropped"] += 1
            return False
    
    def send(self, message: AgentMessage) -> None:
        """
        Envoie un message à un autre agent.
        
        Args:
            message: Message à envoyer
        """
        message.source = self.id
        
        if self._outbox_callback:
            self._outbox_callback(message)
            self._metrics["messages_sent"] += 1
    
    def send_to(
        self,
        target: str,
        msg_type: str,
        payload: Dict[str, Any],
        priority: MessagePriority = MessagePriority.NORMAL,
    ) -> None:
        """
        Envoie un message formaté à un agent cible.
        
        Args:
            target: ID ou nom de l'agent cible
            msg_type: Type de message
            payload: Données du message
            priority: Priorité
        """
        self.send(AgentMessage(
            target=target,
            type=msg_type,
            payload=payload,
            priority=priority,
        ))
    
    def broadcast(
        self,
        msg_type: str,
        payload: Dict[str, Any],
        priority: MessagePriority = MessagePriority.NORMAL,
    ) -> None:
        """
        Diffuse un message à tous les agents.
        
        Args:
            msg_type: Type de message
            payload: Données du message
            priority: Priorité
        """
        self.send(AgentMessage(
            target="",  # Broadcast
            type=msg_type,
            payload=payload,
            priority=priority,
        ))
    
    def set_outbox_callback(self, callback: Callable[[AgentMessage], None]) -> None:
        """Définit le callback pour l'envoi de messages."""
        self._outbox_callback = callback
    
    def register_handler(self, msg_type: str, handler: Callable) -> None:
        """
        Enregistre un handler pour un type de message.
        
        Args:
            msg_type: Type de message
            handler: Fonction de traitement
        """
        self._message_handlers[msg_type] = handler
    
    # === Méthodes abstraites à implémenter ===
    
    @abstractmethod
    async def cycle(self) -> None:
        """
        Cycle principal de l'agent.
        
        Appelé à chaque itération de la boucle principale.
        Doit être implémenté par les agents enfants.
        """
        pass
    
    @abstractmethod
    async def handle_message(self, message: AgentMessage) -> None:
        """
        Traite un message reçu.
        
        Args:
            message: Message à traiter
        """
        pass
    
    # === Hooks optionnels ===
    
    async def on_start(self) -> None:
        """Hook appelé au démarrage."""
        pass
    
    async def on_stop(self) -> None:
        """Hook appelé à l'arrêt."""
        pass
