#!/usr/bin/env python3
"""
Migration script: Add telegram_notifications_enabled field to existing symbol configs.

This script adds the `telegram_notifications_enabled` field (defaulting to True)
to all existing symbol configuration documents that don't have it yet.

Usage:
    python scripts/migrate_add_telegram_notifications.py
"""

import sys
from pathlib import Path

# Add src to path so we can import modules
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root / "src"))

from config import Config
from cosmos_db import CosmosDBService


def main():
    print("Loading configuration...")
    config = Config()
    
    print("Connecting to CosmosDB...")
    cosmos = CosmosDBService(
        endpoint=config.cosmosdb_endpoint,
        key=config.cosmosdb_key,
        database_name=config.cosmosdb_database,
    )
    
    print("Fetching all symbol configs...")
    symbols = cosmos.list_symbols()
    
    updated_count = 0
    skipped_count = 0
    
    for symbol_doc in symbols:
        symbol = symbol_doc["symbol"]
        
        # Check if field already exists
        if "telegram_notifications_enabled" in symbol_doc:
            print(f"  {symbol}: already has telegram_notifications_enabled field, skipping")
            skipped_count += 1
            continue
        
        # Add the field with default value True
        symbol_doc["telegram_notifications_enabled"] = True
        
        # Update the document
        cosmos.container.replace_item(item=symbol_doc["id"], body=symbol_doc)
        print(f"  {symbol}: added telegram_notifications_enabled=True")
        updated_count += 1
    
    print("\n" + "=" * 60)
    print(f"Migration complete!")
    print(f"  Updated: {updated_count}")
    print(f"  Skipped: {skipped_count}")
    print(f"  Total:   {updated_count + skipped_count}")
    print("=" * 60)


if __name__ == "__main__":
    main()
