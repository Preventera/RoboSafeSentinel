"""
OrchestratorAgent - Agent d'orchestration (Niveau 5).

Responsabilités:
- Coordonner tous les agents
- Arbitrer les conflits de recommandations
- Exécuter les actions approuvées
- Gérer les escalades
- Assurer la traçabilité

Inputs:
- Recommandations de DecisionAgent
- Commandes opérateur

Outputs:
- Commandes vers équipements (via interfaces)
- Logs d'audit
- Alertes HMI
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import IntEnum
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4
import structlog

from robosafe.agents.base_agent import (
    BaseAgent,
    AgentConfig,
    AgentLevel,
    AgentMessage,
    MessagePriority,
)

logger = structlog.get_logger(__name__)


class ExecutionStatus(IntEnum):
    """Statut d'exécution d'une action."""
    PENDING = 0
    APPROVED = 1
    EXECUTING = 2
    SUCCESS = 3
    FAILED = 4
    CANCELLED = 5
    TIMEOUT = 6


@dataclass
class ExecutionRecord:
    """Enregistrement d'exécution d'action."""
    id: str
    recommendation_id: str
    action: str
    status: ExecutionStatus
    started_at: datetime
    completed_at: Optional[datetime] = None
    result: str = ""
    operator_id: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "recommendation_id": self.recommendation_id,
            "action": self.action,
            "status": self.status.name,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "result": self.result,
        }


@dataclass
class OrchestratorConfig(AgentConfig):
    """Configuration de l'orchestrateur."""
    name: str = "orchestrator"
    level: AgentLevel = AgentLevel.ORCHESTRATE
    cycle_time_ms: int = 50  # 20 Hz
    
    # Timeouts
    action_timeout_s: float = 5.0
    escalation_timeout_s: float = 10.0
    
    # Arbitrage
    priority_window_ms: float = 200  # Fenêtre pour collecter les recommandations
    
    # Mode
    require_operator_for_stop: bool = False  # Exiger confirmation pour STOP
    auto_recovery_enabled: bool = True


class OrchestratorAgent(BaseAgent):
    """
    Agent d'orchestration - Niveau 5 AgenticX5.
    
    Coordonne l'ensemble du système de sécurité.
    
    Responsabilités:
    1. Recevoir les recommandations des agents de décision
    2. Arbitrer en cas de recommandations conflictuelles
    3. Exécuter les actions via les interfaces appropriées
    4. Gérer les escalades vers opérateur
    5. Maintenir l'audit trail complet
    """
    
    # Message types
    MSG_EXECUTION_RESULT = "execution_result"
    MSG_OPERATOR_ALERT = "operator_alert"
    MSG_SYSTEM_STATE = "system_state"
    MSG_AUDIT_LOG = "audit_log"
    
    def __init__(self, config: Optional[OrchestratorConfig] = None):
        super().__init__(config or OrchestratorConfig())
        self.config: OrchestratorConfig = self.config
        
        # Files de recommandations
        self._pending_recommendations: List[Dict[str, Any]] = []
        
        # Exécutions en cours
        self._active_executions: Dict[str, ExecutionRecord] = {}
        
        # Historique d'audit
        self._audit_log: List[Dict[str, Any]] = []
        self._max_audit_entries = 10000
        
        # Callbacks d'exécution
        self._action_executors: Dict[str, Callable] = {}
        
        # État du système
        self._system_state = {
            "current_action": None,
            "safety_state": "UNKNOWN",
            "last_action_time": None,
        }
        
        # Stats
        self._actions_executed = 0
        self._actions_failed = 0
        self._escalations = 0
        
        # Enregistrer les handlers
        self.register_handler("action_recommendation", self._handle_recommendation)
        self.register_handler("operator_command", self._handle_operator_command)
    
    async def on_start(self) -> None:
        """Initialisation au démarrage."""
        # Enregistrer les exécuteurs par défaut
        self._register_default_executors()
        
        self._log_audit("system_start", "Orchestrator started")
    
    async def on_stop(self) -> None:
        """Nettoyage à l'arrêt."""
        self._log_audit("system_stop", "Orchestrator stopped")
    
    async def cycle(self) -> None:
        """Cycle principal: arbitre et exécute."""
        # 1. Collecter les recommandations en attente
        await self._collect_recommendations()
        
        # 2. Arbitrer si plusieurs recommandations
        if self._pending_recommendations:
            selected = self._arbitrate_recommendations()
            
            if selected:
                # 3. Exécuter l'action sélectionnée
                await self._execute_action(selected)
        
        # 4. Vérifier les exécutions en cours
        await self._check_active_executions()
        
        # 5. Broadcast état système
        self._broadcast_system_state()
    
    async def handle_message(self, message: AgentMessage) -> None:
        """Traite les messages reçus."""
        handler = self._message_handlers.get(message.type)
        if handler:
            await handler(message)
    
    async def _handle_recommendation(self, message: AgentMessage) -> None:
        """Traite une recommandation d'action."""
        recommendation = message.payload
        
        # Ajouter à la file avec timestamp de réception
        recommendation["received_at"] = datetime.now()
        recommendation["source_agent"] = message.source
        
        self._pending_recommendations.append(recommendation)
        
        logger.debug(
            "recommendation_received",
            agent=self.id,
            action=recommendation.get("action"),
            urgency=recommendation.get("urgency"),
        )
    
    async def _handle_operator_command(self, message: AgentMessage) -> None:
        """Traite une commande opérateur."""
        command = message.payload.get("command")
        operator_id = message.payload.get("operator_id", "unknown")
        reason = message.payload.get("reason", "")
        
        self._log_audit(
            "operator_command",
            f"Operator {operator_id}: {command}",
            {"command": command, "operator": operator_id, "reason": reason},
        )
        
        # Créer une recommandation prioritaire
        operator_rec = {
            "id": f"OP-{uuid4().hex[:8]}",
            "action": command,
            "urgency": "IMMEDIATE",
            "reason": f"Commande opérateur: {reason}",
            "risk_score": 100,
            "confidence": 1.0,
            "auto_execute": True,
            "operator_id": operator_id,
            "received_at": datetime.now(),
        }
        
        # Exécuter immédiatement
        await self._execute_action(operator_rec)
    
    async def _collect_recommendations(self) -> None:
        """Collecte les recommandations dans la fenêtre de priorité."""
        # Les recommandations sont déjà collectées via handle_message
        # Ici on peut ajouter une logique de fenêtrage si nécessaire
        pass
    
    def _arbitrate_recommendations(self) -> Optional[Dict[str, Any]]:
        """
        Arbitre entre plusieurs recommandations.
        
        Règles d'arbitrage:
        1. Priorité maximale (IMMEDIATE > HIGH > NORMAL > LOW)
        2. À priorité égale, score de risque le plus élevé
        3. À score égal, première arrivée
        """
        if not self._pending_recommendations:
            return None
        
        # Définir l'ordre de priorité
        urgency_order = {"IMMEDIATE": 0, "HIGH": 1, "NORMAL": 2, "LOW": 3}
        
        # Trier par priorité puis par score
        sorted_recs = sorted(
            self._pending_recommendations,
            key=lambda r: (
                urgency_order.get(r.get("urgency", "LOW"), 4),
                -r.get("risk_score", 0),
                r.get("received_at", datetime.now()),
            )
        )
        
        # Sélectionner la meilleure
        selected = sorted_recs[0] if sorted_recs else None
        
        # Vider la file
        self._pending_recommendations.clear()
        
        if selected:
            logger.info(
                "recommendation_selected",
                agent=self.id,
                action=selected.get("action"),
                reason=selected.get("reason"),
            )
        
        return selected
    
    async def _execute_action(self, recommendation: Dict[str, Any]) -> None:
        """
        Exécute une action recommandée.
        
        Args:
            recommendation: Recommandation à exécuter
        """
        action = recommendation.get("action", "NONE")
        rec_id = recommendation.get("id", "unknown")
        
        # Créer un record d'exécution
        exec_id = f"EXEC-{uuid4().hex[:8]}"
        record = ExecutionRecord(
            id=exec_id,
            recommendation_id=rec_id,
            action=action,
            status=ExecutionStatus.EXECUTING,
            started_at=datetime.now(),
            operator_id=recommendation.get("operator_id"),
        )
        
        self._active_executions[exec_id] = record
        
        try:
            # Trouver l'exécuteur approprié
            executor = self._action_executors.get(action)
            
            if executor:
                # Exécuter l'action
                success = await executor(recommendation)
                
                record.status = ExecutionStatus.SUCCESS if success else ExecutionStatus.FAILED
                record.result = "Action executed successfully" if success else "Execution failed"
                
                self._actions_executed += 1
            else:
                # Pas d'exécuteur, simuler
                record.status = ExecutionStatus.SUCCESS
                record.result = f"Action {action} simulated (no executor)"
                
                logger.warning(
                    "no_executor",
                    agent=self.id,
                    action=action,
                )
            
        except Exception as e:
            record.status = ExecutionStatus.FAILED
            record.result = str(e)
            self._actions_failed += 1
            
            logger.error(
                "execution_error",
                agent=self.id,
                action=action,
                error=str(e),
            )
        
        record.completed_at = datetime.now()
        
        # Log d'audit
        self._log_audit(
            "action_executed",
            f"Action {action}: {record.status.name}",
            {
                "exec_id": exec_id,
                "recommendation_id": rec_id,
                "action": action,
                "status": record.status.name,
                "duration_ms": (record.completed_at - record.started_at).total_seconds() * 1000,
            },
        )
        
        # Mettre à jour l'état système
        self._system_state["current_action"] = action
        self._system_state["last_action_time"] = datetime.now()
        
        # Broadcast le résultat
        self.broadcast(
            msg_type=self.MSG_EXECUTION_RESULT,
            payload=record.to_dict(),
            priority=MessagePriority.HIGH,
        )
    
    async def _check_active_executions(self) -> None:
        """Vérifie les exécutions en cours pour timeouts."""
        now = datetime.now()
        timeout = timedelta(seconds=self.config.action_timeout_s)
        
        for exec_id, record in list(self._active_executions.items()):
            if record.status == ExecutionStatus.EXECUTING:
                if now - record.started_at > timeout:
                    record.status = ExecutionStatus.TIMEOUT
                    record.completed_at = now
                    record.result = "Execution timed out"
                    
                    self._log_audit(
                        "execution_timeout",
                        f"Action {record.action} timed out",
                        {"exec_id": exec_id},
                    )
        
        # Nettoyer les anciennes exécutions
        cutoff = now - timedelta(minutes=5)
        self._active_executions = {
            k: v for k, v in self._active_executions.items()
            if v.completed_at is None or v.completed_at > cutoff
        }
    
    def _broadcast_system_state(self) -> None:
        """Broadcast l'état du système."""
        self.broadcast(
            msg_type=self.MSG_SYSTEM_STATE,
            payload={
                "state": self._system_state,
                "active_executions": len(self._active_executions),
                "actions_executed": self._actions_executed,
                "actions_failed": self._actions_failed,
                "timestamp": datetime.now().isoformat(),
            },
            priority=MessagePriority.NORMAL,
        )
    
    def _log_audit(
        self,
        event_type: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Ajoute une entrée au log d'audit."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "event_type": event_type,
            "message": message,
            "details": details or {},
            "agent": self.id,
        }
        
        self._audit_log.append(entry)
        
        # Limiter la taille
        if len(self._audit_log) > self._max_audit_entries:
            self._audit_log = self._audit_log[-self._max_audit_entries:]
        
        # Broadcast pour persistance externe
        self.broadcast(
            msg_type=self.MSG_AUDIT_LOG,
            payload=entry,
            priority=MessagePriority.LOW,
        )
    
    def _register_default_executors(self) -> None:
        """Enregistre les exécuteurs par défaut."""
        async def log_executor(rec: Dict) -> bool:
            logger.info("action_log", reason=rec.get("reason"))
            return True
        
        async def alert_executor(rec: Dict) -> bool:
            self.broadcast(
                msg_type=self.MSG_OPERATOR_ALERT,
                payload={"alert": rec.get("reason"), "level": "WARNING"},
                priority=MessagePriority.HIGH,
            )
            return True
        
        self._action_executors["LOG"] = log_executor
        self._action_executors["ALERT"] = alert_executor
        self._action_executors["NONE"] = lambda _: True
    
    def register_executor(
        self,
        action: str,
        executor: Callable[[Dict[str, Any]], bool],
    ) -> None:
        """
        Enregistre un exécuteur pour une action.
        
        Args:
            action: Nom de l'action (SLOW_50, STOP, etc.)
            executor: Fonction async qui exécute l'action
        """
        self._action_executors[action] = executor
        logger.info("executor_registered", action=action)
    
    def get_audit_log(
        self,
        limit: int = 100,
        event_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Récupère le log d'audit.
        
        Args:
            limit: Nombre max d'entrées
            event_type: Filtrer par type d'événement
        """
        entries = self._audit_log
        
        if event_type:
            entries = [e for e in entries if e["event_type"] == event_type]
        
        return entries[-limit:]
    
    def get_execution_history(self) -> List[ExecutionRecord]:
        """Récupère l'historique des exécutions."""
        return list(self._active_executions.values())
