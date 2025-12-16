# 🤖 RoboSafeSentinel

**Système de sécurité intelligent pour cellules robotisées industrielles**

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: Proprietary](https://img.shields.io/badge/License-Proprietary-red.svg)](LICENSE)
[![ISO 10218](https://img.shields.io/badge/ISO-10218:2025-green.svg)](docs/compliance/)
[![AgenticX5](https://img.shields.io/badge/AgenticX5-Powered-purple.svg)](https://genaisafety.com)

---

## 🎯 Vue d'ensemble

RoboSafeSentinel est un module de sécurité basé sur l'IA qui s'intègre aux cellules robotisées existantes pour :

- **Détecter** les situations dangereuses en temps réel (intrusions, fumées, postures)
- **Prévenir** les accidents par des interventions graduées (alertes → ralentissement → arrêt)
- **Tracer** tous les événements pour conformité et analyse
- **Améliorer** continuellement via apprentissage des patterns

### Architecture AgenticX5

```
┌─────────────────────────────────────────────────────────────┐
│                    ROBOSAFE SENTINEL                         │
├─────────────────────────────────────────────────────────────┤
│  NIVEAU 5: ORCHESTRATION                                     │
│  └─ Coordination globale, arbitrage, escalade               │
├─────────────────────────────────────────────────────────────┤
│  NIVEAU 4: RECOMMANDATION                                    │
│  └─ Décisions d'intervention (SLOW/STOP/ALERT)              │
├─────────────────────────────────────────────────────────────┤
│  NIVEAU 3: ANALYSE                                           │
│  └─ Évaluation risques, scoring, patterns                   │
├─────────────────────────────────────────────────────────────┤
│  NIVEAU 2: NORMALISATION                                     │
│  └─ Fusion capteurs, cohérence, timestamps                  │
├─────────────────────────────────────────────────────────────┤
│  NIVEAU 1: COLLECTE                                          │
│  └─ Signaux robot, PLC, vision, fumées, wearables          │
└─────────────────────────────────────────────────────────────┘
```

---

## 🚀 Quick Start

### Prérequis

- Python 3.10+
- Accès réseau aux équipements (PLC, robot, capteurs)
- Ubuntu 22.04 LTS (Edge Node) ou Windows 10/11 (développement)

### Installation

```bash
# Cloner le repository
git clone https://github.com/Preventera/RoboSafeSentinel.git
cd RoboSafeSentinel

# Créer environnement virtuel
python -m venv venv
source venv/bin/activate  # Linux
# ou: venv\Scripts\activate  # Windows

# Installer dépendances
pip install -r requirements.txt

# Installer en mode développement
pip install -e .
```

### Configuration

```bash
# Copier la configuration exemple
cp config/config.example.yaml config/config.yaml

# Éditer selon votre cellule
nano config/config.yaml
```

### Lancement

```bash
# Mode simulation (sans équipements réels)
python -m robosafe.main --mode simulation

# Mode production
python -m robosafe.main --config config/config.yaml
```

---

## 📁 Structure du projet

```
RoboSafeSentinel/
├── src/
│   └── robosafe/
│       ├── core/              # Noyau système
│       │   ├── state_machine.py    # Machine d'états sécurité
│       │   ├── signal_manager.py   # Gestion signaux temps réel
│       │   ├── rule_engine.py      # Moteur de règles
│       │   └── watchdog.py         # Surveillance heartbeats
│       ├── agents/            # Agents AgenticX5
│       │   ├── perception.py       # Agent collecte (Niveau 1-2)
│       │   ├── analysis.py         # Agent analyse (Niveau 3)
│       │   ├── decision.py         # Agent décision (Niveau 4)
│       │   └── orchestrator.py     # Agent orchestration (Niveau 5)
│       ├── sensors/           # Drivers capteurs
│       │   ├── robot_fanuc.py      # Interface Fanuc
│       │   ├── plc_siemens.py      # Interface S7-1500
│       │   ├── scanner_sick.py     # Scanner laser SICK
│       │   ├── vision_ai.py        # Caméra vision IA
│       │   └── fumes_sensor.py     # Capteur fumées
│       ├── safety/            # Fonctions sécurité
│       │   ├── ssm_calculator.py   # Calcul distances SSM
│       │   ├── pfl_monitor.py      # Surveillance PFL
│       │   └── exposure_tracker.py # Tracking expositions
│       ├── rules/             # Règles d'intervention
│       │   ├── rules_critical.py   # Règles P0 (E-STOP)
│       │   ├── rules_stop.py       # Règles P1 (STOP)
│       │   ├── rules_slow.py       # Règles P2 (SLOW)
│       │   └── rules_alert.py      # Règles P3 (ALERT)
│       ├── api/               # API REST/WebSocket
│       │   ├── server.py           # Serveur FastAPI
│       │   └── routes.py           # Endpoints
│       └── utils/             # Utilitaires
│           ├── logger.py           # Logging structuré
│           ├── config.py           # Gestion configuration
│           └── metrics.py          # Métriques Prometheus
├── tests/                     # Tests
│   ├── unit/                  # Tests unitaires
│   └── integration/           # Tests intégration
├── docs/                      # Documentation
│   ├── architecture/          # Schémas architecture
│   ├── compliance/            # Documents conformité
│   └── training/              # Supports formation
├── config/                    # Fichiers configuration
├── scripts/                   # Scripts utilitaires
├── data/                      # Données
│   ├── templates/             # Templates Excel
│   └── samples/               # Données exemple
└── assets/                    # Ressources
```

---

## ⚙️ Configuration

### Exemple `config.yaml`

```yaml
cell:
  id: "WELD-MIG-001"
  name: "Cellule Soudage MIG"
  type: "welding"

robot:
  type: "fanuc"
  model: "ARC Mate 100iD"
  ip: "192.168.1.10"
  protocol: "ethernet_ip"

plc:
  type: "siemens"
  model: "S7-1500F"
  ip: "192.168.1.20"
  protocol: "profisafe"

sensors:
  scanners:
    - id: "scanner_left"
      type: "sick_microscan3"
      ip: "192.168.1.30"
    - id: "scanner_right"
      type: "sick_microscan3"
      ip: "192.168.1.31"
  
  vision:
    enabled: true
    ip: "192.168.1.40"
    model: "basler_ace2"
  
  fumes:
    enabled: true
    ip: "192.168.1.50"
    protocol: "modbus_tcp"
    vlep: 5.0  # mg/m³

thresholds:
  fumes:
    warning: 0.5   # 50% VLEP
    alert: 0.8     # 80% VLEP
    critical: 1.0  # 100% VLEP
    stop: 1.2      # 120% VLEP
  
  distance:
    stop: 800      # mm
    slow: 1500     # mm
    warn: 2000     # mm

logging:
  level: "INFO"
  format: "json"
  output: "logs/robosafe.log"
```

---

## 🔒 Sécurité

### Principe fondamental

> **La chaîne de sécurité certifiée (PLC Safety) reste SOUVERAINE.**
> 
> RoboSafeSentinel est une couche fonctionnelle additionnelle qui peut DEMANDER des actions mais ne peut jamais COMMANDER directement ni INHIBER les protections certifiées.

### Fail-safe

| Événement | Action |
|-----------|--------|
| Perte communication IA | Fallback PLC sécurité seul |
| Timeout heartbeat | Arrêt surveillé automatique |
| Erreur critique | Mode dégradé sécuritaire |

---

## 📊 KPI & Monitoring

### Dashboard temps réel

```
http://localhost:8080/dashboard
```

### Métriques Prometheus

```
http://localhost:9090/metrics
```

### KPI disponibles

- `robosafe_state` - État machine (NORMAL/WARNING/SLOW/STOP/ESTOP)
- `robosafe_risk_score` - Score risque 0-100
- `robosafe_fumes_vlep_ratio` - Ratio fumées/VLEP
- `robosafe_interventions_total` - Compteur interventions
- `robosafe_false_positives_total` - Compteur faux positifs

---

## 🧪 Tests

```bash
# Tous les tests
pytest

# Tests unitaires uniquement
pytest tests/unit/

# Tests avec couverture
pytest --cov=robosafe --cov-report=html

# Tests d'intégration (nécessite équipements)
pytest tests/integration/ --integration
```

---

## 📚 Documentation

| Document | Description |
|----------|-------------|
| [Architecture](docs/architecture/) | Schémas et diagrammes |
| [Compliance](docs/compliance/) | Conformité ISO 10218, 13849 |
| [Training](docs/training/) | Supports de formation |
| [API Reference](docs/api/) | Documentation API REST |

---

## 🤝 Contribution

1. Fork le repository
2. Créer une branche feature (`git checkout -b feature/AmazingFeature`)
3. Commit les changements (`git commit -m 'Add AmazingFeature'`)
4. Push la branche (`git push origin feature/AmazingFeature`)
5. Ouvrir une Pull Request

---

## 📄 Licence

Propriétaire - © 2024-2025 Preventera / GenAISafety

---

## 📞 Support

- **Email**: support@genaisafety.com
- **Documentation**: https://docs.genaisafety.com/robosafe
- **Issues**: https://github.com/Preventera/RoboSafeSentinel/issues

---

*Développé avec ❤️ par l'équipe SquadrAI*
