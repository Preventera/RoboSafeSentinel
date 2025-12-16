"""
Gestionnaire de connexions WebSocket.

Gère les connexions clients et le broadcast des messages temps réel.
"""

import asyncio
import json
from datetime import datetime
from typing import Any, Dict, List, Set
from fastapi import WebSocket
import structlog

logger = structlog.get_logger(__name__)


class WebSocketManager:
    """
    Gestionnaire de connexions WebSocket.
    
    Fonctionnalités:
    - Connexion/déconnexion clients
    - Broadcast à tous les clients
    - Envoi ciblé à un client
    - Gestion des groupes/rooms
    """
    
    def __init__(self):
        self._connections: Set[WebSocket] = set()
        self._rooms: Dict[str, Set[WebSocket]] = {}
        self._client_info: Dict[WebSocket, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        
        # Stats
        self._total_connections = 0
        self._total_messages_sent = 0
    
    @property
    def client_count(self) -> int:
        """Nombre de clients connectés."""
        return len(self._connections)
    
    @property
    def stats(self) -> Dict[str, int]:
        """Statistiques WebSocket."""
        return {
            "current_connections": len(self._connections),
            "total_connections": self._total_connections,
            "total_messages_sent": self._total_messages_sent,
            "rooms": len(self._rooms),
        }
    
    async def connect(
        self, 
        websocket: WebSocket,
        client_id: str = None,
        rooms: List[str] = None,
    ) -> None:
        """
        Accepte une nouvelle connexion WebSocket.
        
        Args:
            websocket: Connexion WebSocket
            client_id: ID optionnel du client
            rooms: Rooms à rejoindre
        """
        await websocket.accept()
        
        async with self._lock:
            self._connections.add(websocket)
            self._total_connections += 1
            
            # Info client
            self._client_info[websocket] = {
                "client_id": client_id,
                "connected_at": datetime.now(),
                "rooms": rooms or [],
            }
            
            # Rejoindre rooms
            if rooms:
                for room in rooms:
                    if room not in self._rooms:
                        self._rooms[room] = set()
                    self._rooms[room].add(websocket)
        
        logger.info(
            "websocket_connected",
            client_id=client_id,
            total_clients=self.client_count,
        )
        
        # Envoyer message de bienvenue
        await self.send_personal(websocket, {
            "type": "connected",
            "timestamp": datetime.now().isoformat(),
            "client_count": self.client_count,
        })
    
    def disconnect(self, websocket: WebSocket) -> None:
        """
        Déconnecte un client.
        
        Args:
            websocket: Connexion à fermer
        """
        if websocket in self._connections:
            self._connections.discard(websocket)
            
            # Retirer des rooms
            info = self._client_info.pop(websocket, {})
            for room in info.get("rooms", []):
                if room in self._rooms:
                    self._rooms[room].discard(websocket)
            
            logger.info(
                "websocket_disconnected",
                client_id=info.get("client_id"),
                total_clients=self.client_count,
            )
    
    async def disconnect_all(self) -> None:
        """Déconnecte tous les clients."""
        async with self._lock:
            for websocket in list(self._connections):
                try:
                    await websocket.close()
                except Exception:
                    pass
            
            self._connections.clear()
            self._rooms.clear()
            self._client_info.clear()
        
        logger.info("websocket_all_disconnected")
    
    async def send_personal(
        self, 
        websocket: WebSocket, 
        message: Dict[str, Any]
    ) -> bool:
        """
        Envoie un message à un client spécifique.
        
        Args:
            websocket: Client cible
            message: Message à envoyer
            
        Returns:
            True si envoyé avec succès
        """
        try:
            await websocket.send_json(message)
            self._total_messages_sent += 1
            return True
        except Exception as e:
            logger.debug("websocket_send_error", error=str(e))
            self.disconnect(websocket)
            return False
    
    async def broadcast(
        self, 
        message: Dict[str, Any],
        exclude: WebSocket = None,
    ) -> int:
        """
        Envoie un message à tous les clients.
        
        Args:
            message: Message à envoyer
            exclude: Client à exclure (optionnel)
            
        Returns:
            Nombre de clients qui ont reçu le message
        """
        if not self._connections:
            return 0
        
        sent_count = 0
        failed = []
        
        for connection in list(self._connections):
            if connection == exclude:
                continue
            
            try:
                await connection.send_json(message)
                sent_count += 1
                self._total_messages_sent += 1
            except Exception:
                failed.append(connection)
        
        # Nettoyer les connexions échouées
        for conn in failed:
            self.disconnect(conn)
        
        return sent_count
    
    async def broadcast_to_room(
        self, 
        room: str, 
        message: Dict[str, Any]
    ) -> int:
        """
        Envoie un message à tous les clients d'une room.
        
        Args:
            room: Nom de la room
            message: Message à envoyer
            
        Returns:
            Nombre de clients qui ont reçu le message
        """
        if room not in self._rooms:
            return 0
        
        sent_count = 0
        failed = []
        
        for connection in list(self._rooms[room]):
            try:
                await connection.send_json(message)
                sent_count += 1
                self._total_messages_sent += 1
            except Exception:
                failed.append(connection)
        
        for conn in failed:
            self.disconnect(conn)
        
        return sent_count
    
    async def join_room(self, websocket: WebSocket, room: str) -> None:
        """Ajoute un client à une room."""
        async with self._lock:
            if room not in self._rooms:
                self._rooms[room] = set()
            self._rooms[room].add(websocket)
            
            if websocket in self._client_info:
                self._client_info[websocket].setdefault("rooms", []).append(room)
    
    async def leave_room(self, websocket: WebSocket, room: str) -> None:
        """Retire un client d'une room."""
        async with self._lock:
            if room in self._rooms:
                self._rooms[room].discard(websocket)
            
            if websocket in self._client_info:
                rooms = self._client_info[websocket].get("rooms", [])
                if room in rooms:
                    rooms.remove(room)
    
    def get_room_clients(self, room: str) -> int:
        """Retourne le nombre de clients dans une room."""
        return len(self._rooms.get(room, set()))
