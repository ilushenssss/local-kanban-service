import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kanban.store import Store

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Проверенная резервная копия SQLite и вложений')
    parser.add_argument('data')
    parser.add_argument('destination')
    parser.add_argument('--retention-days', type=int, default=14)
    args = parser.parse_args()
    print(Store(args.data).backup(args.destination, args.retention_days))
