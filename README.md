# 🤖 RoboSafe Sentinel

**Système de supervision sécurité temps réel pour cellules robotisées industrielles**

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Tests](https://img.shields.io/badge/tests-92%20passing-green.svg)]()

---

## 🎯 Vue d'ensemble

RoboSafe Sentinel est une plateforme de supervision de sécurité basée sur l'architecture **AgenticX5** à 5 niveaux d'agents intelligents. Elle assure la protection des opérateurs travaillant à proximité de robots industriels en :

- 📡 **Collectant** les données de multiples capteurs en temps réel
- 🔍 **Analysant** les risques (distance, collision, exposition, équipement)
- ⚡ **Décidant** des actions de sécurité appropriées
- 🎯 **Exécutant** les commandes (E-STOP, ralentissement, alertes)

---

## 🏗️ Architecture AgenticX5

```
┌─────────────────────────────────────────────────────────────────────┐
│                    NIVEAU 5: ORCHESTRATEUR                         │
│         Coordination, arbitrage, exécution des actions             │
├─────────────────────────────────────────────────────────────────────┤
│                    NIVEAU 4: DÉCISION                              │
│         Recommandations d'actions basées sur les risques           │
├─────────────────────────────────────────────────────────────────────┤
│                    NIVEAU 3: ANALYSE                               │
│         Scoring des risques, détection de patterns                 │
├─────────────────────────────────────────────────────────────────────┤
│                  NIVEAUX 1-2: PERCEPTION                           │
│         Collecte, normalisation, filtrage des signaux              │
└─────────────────────────────────────────────────────────────────────┘
                              ▲
                              │
        ┌─────────┬─────────┬─────────┬─────────┬─────────┐
        │   PLC   │  Robot  │ Scanner │ Fumées  │ Vision  │
        │ Siemens │  Fanuc  │  SICK   │ Modbus  │  YOLO   │
        └─────────┴─────────┴─────────┴─────────┴─────────┘
```

---

## 📦 Installation

### Prérequis

- Python 3.11 ou supérieur
- Windows 10/11 ou Linux
- Git

### Installation rapide

```powershell
# Cloner le repository
git clone https://github.com/Preventera/RoboSafeSentinel.git
cd RoboSafeSentinel

# Créer un environnement virtuel (optionnel mais recommandé)
python -m venv venv
.\venv\Scripts\Activate.ps1  # Windows
# source venv/bin/activate   # Linux/Mac

# Installer le package
pip install -e .

# Vérifier l'installation
python -c "import robosafe; print('✅ Installation OK')"
```

### Dépendances principales

| Package | Version | Usage |
|---------|---------|-------|
| FastAPI | 0.104+ | API REST |
| uvicorn | 0.24+ | Serveur ASGI |
| structlog | 23.2+ | Logging structuré |
| ultralytics | 8.0+ | Vision IA (YOLO) |
| numpy | 1.24+ | Calculs numériques |
| prometheus-client | 0.19+ | Métriques |

---

## 🚀 Démarrage rapide

### Mode Simulation (sans matériel)

```powershell
# Lancer avec simulateurs
python -m robosafe.integration --simulate --port 9000
```

### Accès aux interfaces

| URL | Description |
|-----|-------------|
| http://localhost:9000/docs | 📚 Documentation API (Swagger) |
| http://localhost:9000/health | ❤️ Health check |
| http://localhost:9000/api/v1/status | 📊 État du système |
| http://localhost:9000/api/v1/signals | 📡 Signaux temps réel |
| http://localhost:9000/metrics | 📈 Métriques Prometheus |
| http://localhost:9000/static/dashboard.html | 🖥️ Dashboard |

### Mode Production

```powershell
# Avec fichier de configuration
python -m robosafe.integration --config config/production.yaml
```

---

## 📡 Capteurs supportés

### PLC Siemens S7-1500F
- Communication: S7 Protocol (TCP/IP)
- Signaux: E-STOP, portes, verrous, heartbeat
- Cycle: 100ms

### Robot Fanuc ARC Mate
- Communication: EtherNet/IP
- Signaux: Position TCP, vitesse, mode, alarmes
- Cycle: 50ms

### Scanner SICK microScan3
- Communication: SICK SOPAS (TCP)
- Signaux: Distance minimale, zones actives, contamination
- Cycle: 100ms

### Capteur de fumées
- Communication: Modbus TCP
- Signaux: Concentration, ratio VLEP, température
- Cycle: 500ms

### Vision IA
- Modèle: YOLOv8
- Détection: Personnes, EPI, intrusions
- Cycle: 33ms (30 FPS)

---

## ⚙️ Règles de sécurité

Le système embarque 8 règles de sécurité préconfigurées :

| ID | Règle | Condition | Action |
|----|-------|-----------|--------|
| RS-001 | Distance critique | < 500mm | **E-STOP** |
| RS-002 | Distance warning | 500-800mm | Ralentir 25% |
| RS-003 | Distance monitoring | 800-1200mm | Ralentir 50% |
| RS-004 | Fumées critiques | > 120% VLEP | **STOP** |
| RS-005 | Fumées élevées | 80-120% VLEP | Alerte |
| RS-006 | Intrusion vision | Zone danger | **E-STOP** |
| RS-007 | EPI manquant | Détection | Alerte |
| RS-008 | E-STOP physique | Bouton activé | **E-STOP** |

---

## 🔌 API REST

### Endpoints principaux

```http
GET  /health              # Health check
GET  /api/v1/status       # État complet du système
GET  /api/v1/signals      # Tous les signaux
GET  /api/v1/signals/{id} # Signal spécifique
GET  /api/v1/alerts       # Alertes actives
POST /api/v1/command      # Envoyer une commande
GET  /api/v1/rules        # Liste des règles
POST /api/v1/rules/{id}/enable   # Activer une règle
POST /api/v1/rules/{id}/disable  # Désactiver une règle
GET  /metrics             # Métriques Prometheus
```

### Exemple de requête

```bash
# Obtenir l'état du système
curl http://localhost:9000/api/v1/status

# Envoyer une commande
curl -X POST http://localhost:9000/api/v1/command \
  -H "Content-Type: application/json" \
  -d '{"command": "RESET", "source": "operator"}'
```

---

## 📊 WebSocket

Connexion temps réel pour le dashboard :

```javascript
const ws = new WebSocket('ws://localhost:9000/ws');

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log('Type:', data.type);
  console.log('Payload:', data.payload);
};
```

### Types de messages

| Type | Description |
|------|-------------|
| `status` | État périodique du système |
| `risk_update` | Mise à jour des scores de risque |
| `alert` | Nouvelle alerte |
| `execution_result` | Résultat d'exécution d'action |

---

## 🧪 Tests

### Exécuter tous les tests

```powershell
pytest tests/ -v
```

### Tests par module

```powershell
# Tests des capteurs
pytest tests/unit/test_sensors.py -v

# Tests de la vision
pytest tests/unit/test_vision.py -v

# Tests des agents
pytest tests/unit/test_agents.py -v

# Tests de l'API
pytest tests/unit/test_api.py -v
```

### Couverture

```powershell
pytest tests/ --cov=robosafe --cov-report=html
```

---

## 📁 Structure du projet

```
RoboSafeSentinel/
├── src/robosafe/
│   ├── agents/           # Agents AgenticX5
│   │   ├── base_agent.py
│   │   ├── perception_agent.py
│   │   ├── analysis_agent.py
│   │   ├── decision_agent.py
│   │   └── orchestrator_agent.py
│   ├── api/              # API REST & WebSocket
│   │   ├── server.py
│   │   ├── websocket_manager.py
│   │   ├── metrics.py
│   │   └── static/
│   ├── core/             # Composants centraux
│   │   ├── state_machine.py
│   │   ├── signal_manager.py
│   │   └── rule_engine.py
│   ├── sensors/          # Drivers capteurs
│   │   ├── plc_siemens.py
│   │   ├── robot_fanuc.py
│   │   ├── scanner_sick.py
│   │   ├── fumes_sensor.py
│   │   └── vision_ai.py
│   └── integration.py    # Script principal
├── tests/
│   └── unit/
├── config/
│   └── production.yaml
├── data/
│   └── templates/        # Fichiers Excel du pilote
├── .github/workflows/    # CI/CD
├── pyproject.toml
└── README.md
```

---

## 🔧 Configuration

### Fichier production.yaml

```yaml
cell:
  id: "WELD-MIG-001"
  name: "Cellule Soudage MIG #1"

plc:
  type: "siemens_s7"
  ip: "192.168.1.10"
  rack: 0
  slot: 1

robot:
  type: "fanuc"
  ip: "192.168.1.20"
  port: 44818

scanner:
  type: "sick_microscan3"
  ip: "192.168.1.30"
  zone_protective_mm: 500
  zone_warning_mm: 1200

agents:
  analysis:
    distance_critical_mm: 500
    distance_high_mm: 800
    fumes_critical_vlep: 1.2
```

---

## 📈 Métriques Prometheus

```prometheus
# Signaux
robosafe_signal_value{signal_id="scanner_min_distance"}

# Agents
robosafe_agent_cycles_total{agent="perception"}
robosafe_agent_messages_total{agent="analysis"}

# Règles
robosafe_rules_triggered_total{rule_id="RS-001"}

# État
robosafe_safety_state{state="NOMINAL"}
```

---

## 🐳 Docker

```bash
# Construire l'image
docker build -t robosafe-sentinel .

# Lancer en mode simulation
docker run -p 9000:9000 robosafe-sentinel --simulate

# Avec configuration montée
docker run -p 9000:9000 \
  -v ./config:/app/config \
  robosafe-sentinel --config /app/config/production.yaml
```

---

## 🤝 Contribution

1. Fork le repository
2. Créer une branche (`git checkout -b feature/nouvelle-fonctionnalite`)
3. Commiter (`git commit -m 'Ajout nouvelle fonctionnalité'`)
4. Pusher (`git push origin feature/nouvelle-fonctionnalite`)
5. Ouvrir une Pull Request

---

## 📄 Licence

MIT License - voir [LICENSE](LICENSE)

---

## 👥 Équipe

**Preventera / GenAISafety**
- Architecture AgenticX5
- Expertise HSE + IA

---

## 📞 Support

- 📧 Email: support@preventera.com
- 🐛 Issues: [GitHub Issues](https://github.com/Preventera/RoboSafeSentinel/issues)
- 📖 Docs: [Documentation complète](https://docs.preventera.com/robosafe)

---

<p align="center">
  <strong>🛡️ La sécurité des travailleurs, augmentée par l'IA</strong>
</p>
