import yaml
from orchestrator.master import MasterOrchestrator

def load_config(path="config/config.yaml"):
    with open(path, 'r') as f:
        return yaml.safe_load(f)

if __name__ == "__main__":
    config = load_config()
    orchestrator = MasterOrchestrator(config)
    orchestrator.run()