#!/usr/bin/env python3
"""
CosmosDB Unified Container Migration Script

Migrates from dual doc_type (activity/alert) model to unified is_alert model:
- Merges alert documents into parent activity documents
- Strips legacy dec_/sig_ prefixes from IDs
- Replaces doc_type discriminator with is_alert boolean

Usage:
    python scripts/migrate_cosmos_events.py [--dry-run] [--restore BACKUP_FILE]
    
Examples:
    # Dry-run (phases 1-2 only, no writes)
    python scripts/migrate_cosmos_events.py --dry-run
    
    # Execute migration
    python scripts/migrate_cosmos_events.py
    
    # Restore from backup
    python scripts/migrate_cosmos_events.py --restore backups/backup_20260401T1430.json
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from typing import Optional

from azure.cosmos import CosmosClient
from azure.cosmos.exceptions import CosmosResourceNotFoundError, CosmosResourceExistsError

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class MigrationError(Exception):
    """Base exception for migration errors."""
    pass


class CosmosEventMigration:
    """Handles migration from activity/alert dual doc_type to unified is_alert model."""
    
    def __init__(self, endpoint: str, key: str, database_name: str = "stock-options-manager"):
        """Initialize CosmosDB client."""
        self.client = CosmosClient(endpoint, credential=key)
        self.database = self.client.get_database_client(database_name)
        self.container = self.database.get_container_client("symbols")
        
        # Migration state
        self.activities = {}
        self.alerts = []
        self.merged_docs = []
        self.orphaned_alerts = []
        self.stats = {
            'activities_before': 0,
            'alerts_before': 0,
            'activities_after': 0,
            'alerts_merged': 0,
            'alerts_orphaned': 0,
            'id_collisions': 0,
        }
    
    def run_migration(self, dry_run: bool = False):
        """Execute the full 4-phase migration."""
        try:
            logger.info("=" * 70)
            logger.info("CosmosDB Unified Container Migration")
            logger.info("=" * 70)
            
            if dry_run:
                logger.info("🔍 DRY-RUN MODE: No writes will be performed")
            
            # Phase 1: Export backup
            logger.info("\n📦 PHASE 1: Export Backup")
            backup_file = self._export_backup()
            logger.info(f"✅ Backup created: {backup_file}")
            
            # Phase 2: Merge and transform
            logger.info("\n🔧 PHASE 2: Merge and Transform")
            self._merge_and_transform()
            logger.info(f"✅ Transformation complete")
            self._print_transformation_summary()
            
            if dry_run:
                logger.info("\n🔍 DRY-RUN COMPLETE: Phases 3-4 skipped")
                logger.info(f"Backup file saved: {backup_file}")
                logger.info("Review the transformation summary above.")
                logger.info("To execute the migration, run without --dry-run flag.")
                return backup_file
            
            # Phase 3: Write unified events
            logger.info("\n✍️  PHASE 3: Write Unified Events")
            self._write_unified_events()
            logger.info(f"✅ Written {len(self.merged_docs)} unified documents")
            
            # Phase 4: Validate
            logger.info("\n✓ PHASE 4: Validate")
            self._validate_migration()
            logger.info("✅ Validation passed")
            
            logger.info("\n" + "=" * 70)
            logger.info("🎉 MIGRATION COMPLETE")
            logger.info("=" * 70)
            self._print_final_summary()
            logger.info(f"Backup file: {backup_file}")
            logger.info("Keep backup for 7 days in case rollback is needed.")
            
            return backup_file
            
        except Exception as e:
            logger.error(f"❌ Migration failed: {e}")
            logger.error("Database may be in inconsistent state!")
            logger.error(f"To rollback, run: python {sys.argv[0]} --restore {backup_file}")
            raise MigrationError(f"Migration failed: {e}")
    
    def _export_backup(self) -> str:
        """Phase 1: Export all activity and alert documents to backup file."""
        timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M")
        backup_dir = "backups"
        os.makedirs(backup_dir, exist_ok=True)
        backup_file = os.path.join(backup_dir, f"backup_{timestamp}.json")
        
        # Query all activities (doc_type='activity')
        logger.info("Querying activities...")
        activities_query = "SELECT * FROM c WHERE c.doc_type = 'activity'"
        activities = list(self.container.query_items(
            query=activities_query,
            enable_cross_partition_query=True
        ))
        self.stats['activities_before'] = len(activities)
        logger.info(f"Found {len(activities)} activity documents")
        
        # Query all alerts (doc_type='alert')
        logger.info("Querying alerts...")
        alerts_query = "SELECT * FROM c WHERE c.doc_type = 'alert'"
        alerts = list(self.container.query_items(
            query=alerts_query,
            enable_cross_partition_query=True
        ))
        self.stats['alerts_before'] = len(alerts)
        logger.info(f"Found {len(alerts)} alert documents")
        
        # Store for later phases
        self.activities = {doc['id']: doc for doc in activities}
        self.alerts = alerts
        
        # Write backup
        backup_data = {
            'timestamp': timestamp,
            'database': self.database.id,
            'container': self.container.id,
            'stats': {
                'activities': len(activities),
                'alerts': len(alerts),
            },
            'documents': {
                'activities': activities,
                'alerts': alerts,
            }
        }
        
        with open(backup_file, 'w') as f:
            json.dump(backup_data, f, indent=2)
        
        # Validate backup integrity
        with open(backup_file, 'r') as f:
            verify = json.load(f)
            if len(verify['documents']['activities']) != len(activities):
                raise MigrationError("Backup validation failed: activity count mismatch")
            if len(verify['documents']['alerts']) != len(alerts):
                raise MigrationError("Backup validation failed: alert count mismatch")
        
        logger.info(f"Backup validated: {len(activities)} activities + {len(alerts)} alerts")
        return backup_file
    
    def _merge_and_transform(self):
        """Phase 2: Merge alerts into activities and transform IDs."""
        logger.info("Merging alerts into parent activities...")
        
        # Process each alert
        for alert_doc in self.alerts:
            alert_id = alert_doc['id']
            activity_id = alert_doc.get('activity_id')
            
            if not activity_id:
                logger.warning(f"Alert {alert_id} missing activity_id field, treating as orphan")
                self._convert_orphaned_alert(alert_doc)
                continue
            
            # Find parent activity
            parent = self.activities.get(activity_id)
            
            if parent:
                # Merge alert data into parent
                parent['is_alert'] = True
                
                # Copy alert-only fields (confidence, etc.)
                for field in ['confidence']:
                    if field in alert_doc:
                        parent[field] = alert_doc[field]
                
                self.stats['alerts_merged'] += 1
                logger.debug(f"Merged alert {alert_id} into activity {activity_id}")
            else:
                # Orphaned alert (no parent activity found)
                logger.warning(f"Orphaned alert {alert_id}: activity {activity_id} not found")
                self._convert_orphaned_alert(alert_doc)
        
        # Transform all activities (strip dec_ prefix, handle duplicates)
        logger.info("Transforming activity IDs...")
        seen_ids = set()
        
        for old_id, activity in self.activities.items():
            # Strip dec_ prefix
            if old_id.startswith('dec_'):
                new_id = old_id[4:]  # Remove 'dec_' (4 chars)
            else:
                new_id = old_id
                logger.debug(f"Activity {old_id} already has no dec_ prefix")
            
            # Handle ID collisions (duplicate timestamps)
            if new_id in seen_ids:
                logger.warning(f"ID collision detected: {new_id}")
                sequence = 2
                while f"{new_id}_{sequence}" in seen_ids:
                    sequence += 1
                new_id = f"{new_id}_{sequence}"
                self.stats['id_collisions'] += 1
                logger.info(f"Resolved collision: {old_id} → {new_id}")
            
            seen_ids.add(new_id)
            
            # Create transformed document
            transformed = {**activity, 'id': new_id}
            
            # Remove activity_id field if present (stale reference)
            transformed.pop('activity_id', None)
            
            # Ensure doc_type is 'activity' (required for unified model)
            transformed['doc_type'] = 'activity'
            
            self.merged_docs.append(transformed)
        
        self.stats['activities_after'] = len(self.merged_docs)
        logger.info(f"Transformed {len(self.merged_docs)} documents")
    
    def _convert_orphaned_alert(self, alert_doc: dict):
        """Convert an orphaned alert to a standalone activity."""
        alert_id = alert_doc['id']
        
        # Strip sig_ prefix
        if alert_id.startswith('sig_'):
            new_id = alert_id[4:]  # Remove 'sig_' (4 chars)
        else:
            new_id = alert_id
        
        # Convert to activity document
        orphan_activity = {
            **alert_doc,
            'id': new_id,
            'doc_type': 'activity',
            'is_alert': True,
        }
        
        # Remove activity_id field
        orphan_activity.pop('activity_id', None)
        
        # Add to activities dict and merged docs
        self.activities[alert_id] = orphan_activity  # Keep old ID as key for tracking
        self.merged_docs.append(orphan_activity)
        self.orphaned_alerts.append({
            'old_id': alert_id,
            'new_id': new_id,
            'symbol': alert_doc.get('symbol', 'UNKNOWN'),
        })
        self.stats['alerts_orphaned'] += 1
        logger.info(f"Converted orphaned alert {alert_id} → {new_id}")
    
    def _write_unified_events(self):
        """Phase 3: Delete old documents and write new unified documents."""
        write_count = 0
        delete_count = 0
        
        # First, write all new documents
        logger.info(f"Writing {len(self.merged_docs)} unified documents...")
        for doc in self.merged_docs:
            try:
                self.container.create_item(doc)
                write_count += 1
                if write_count % 10 == 0:
                    logger.info(f"Written {write_count}/{len(self.merged_docs)} documents...")
            except CosmosResourceExistsError:
                logger.warning(f"Document {doc['id']} already exists, skipping")
            except Exception as e:
                logger.error(f"Failed to write document {doc['id']}: {e}")
                raise MigrationError(f"Write failed for {doc['id']}: {e}")
        
        logger.info(f"Written {write_count} documents")
        
        # Delete old activity documents (with dec_ prefix)
        logger.info("Deleting old activity documents...")
        for old_id, activity in self.activities.items():
            if old_id.startswith('dec_'):
                try:
                    symbol = activity.get('symbol')
                    if not symbol:
                        logger.warning(f"Activity {old_id} missing symbol, cannot delete")
                        continue
                    self.container.delete_item(old_id, partition_key=symbol)
                    delete_count += 1
                except CosmosResourceNotFoundError:
                    logger.debug(f"Activity {old_id} already deleted")
                except Exception as e:
                    logger.error(f"Failed to delete activity {old_id}: {e}")
                    raise MigrationError(f"Delete failed for {old_id}: {e}")
        
        # Delete all alert documents
        logger.info("Deleting old alert documents...")
        for alert_doc in self.alerts:
            alert_id = alert_doc['id']
            symbol = alert_doc.get('symbol')
            if not symbol:
                logger.warning(f"Alert {alert_id} missing symbol, cannot delete")
                continue
            try:
                self.container.delete_item(alert_id, partition_key=symbol)
                delete_count += 1
            except CosmosResourceNotFoundError:
                logger.debug(f"Alert {alert_id} already deleted")
            except Exception as e:
                logger.error(f"Failed to delete alert {alert_id}: {e}")
                raise MigrationError(f"Delete failed for {alert_id}: {e}")
        
        logger.info(f"Deleted {delete_count} old documents")
    
    def _validate_migration(self):
        """Phase 4: Validate migration success."""
        logger.info("Running validation checks...")
        
        # Check 1: Count activities (should match merged docs)
        activities_query = "SELECT * FROM c WHERE c.doc_type = 'activity'"
        activities_after = list(self.container.query_items(
            query=activities_query,
            enable_cross_partition_query=True
        ))
        activities_count = len(activities_after)
        
        expected_count = len(self.merged_docs)
        if activities_count != expected_count:
            raise MigrationError(
                f"Activity count mismatch: expected {expected_count}, found {activities_count}"
            )
        logger.info(f"✓ Activity count: {activities_count} (expected {expected_count})")
        
        # Check 2: Count is_alert=true activities (should match alerts merged + orphaned)
        alerts_query = "SELECT * FROM c WHERE c.doc_type = 'activity' AND c.is_alert = true"
        alerts_after = list(self.container.query_items(
            query=alerts_query,
            enable_cross_partition_query=True
        ))
        alerts_count = len(alerts_after)
        
        expected_alerts = self.stats['alerts_before']
        if alerts_count != expected_alerts:
            logger.warning(
                f"Alert count mismatch: expected {expected_alerts}, found {alerts_count}"
            )
            logger.warning("This may be expected if some activities were already marked is_alert=true")
        else:
            logger.info(f"✓ Alert count: {alerts_count} (expected {expected_alerts})")
        
        # Check 3: No doc_type='alert' documents remain
        old_alerts_query = "SELECT * FROM c WHERE c.doc_type = 'alert'"
        old_alerts = list(self.container.query_items(
            query=old_alerts_query,
            enable_cross_partition_query=True
        ))
        if old_alerts:
            raise MigrationError(f"Found {len(old_alerts)} doc_type='alert' documents still present")
        logger.info("✓ No doc_type='alert' documents remain")
        
        # Check 4: No IDs with dec_ or sig_ prefixes
        prefix_query = "SELECT c.id FROM c WHERE STARTSWITH(c.id, 'dec_') OR STARTSWITH(c.id, 'sig_')"
        prefixed_docs = list(self.container.query_items(
            query=prefix_query,
            enable_cross_partition_query=True
        ))
        if prefixed_docs:
            raise MigrationError(f"Found {len(prefixed_docs)} documents with dec_/sig_ prefixes")
        logger.info("✓ No dec_/sig_ prefixed IDs remain")
        
        # Check 5: Spot-check merged records
        logger.info("Spot-checking merged records...")
        if alerts_after:
            sample_size = min(3, len(alerts_after))
            samples = alerts_after[:sample_size]
            for doc in samples:
                if not doc.get('is_alert'):
                    raise MigrationError(f"Document {doc['id']} missing is_alert flag")
                if doc.get('doc_type') != 'activity':
                    raise MigrationError(f"Document {doc['id']} has wrong doc_type: {doc.get('doc_type')}")
            logger.info(f"✓ Spot-checked {sample_size} merged alert records")
        
        logger.info("All validation checks passed")
    
    def _print_transformation_summary(self):
        """Print summary of transformation phase."""
        logger.info("\n" + "-" * 70)
        logger.info("TRANSFORMATION SUMMARY")
        logger.info("-" * 70)
        logger.info(f"Activities before:     {self.stats['activities_before']}")
        logger.info(f"Alerts before:         {self.stats['alerts_before']}")
        logger.info(f"Alerts merged:         {self.stats['alerts_merged']}")
        logger.info(f"Alerts orphaned:       {self.stats['alerts_orphaned']}")
        logger.info(f"ID collisions:         {self.stats['id_collisions']}")
        logger.info(f"Documents after:       {self.stats['activities_after']}")
        
        if self.orphaned_alerts:
            logger.info("\nOrphaned alerts converted to standalone activities:")
            for orphan in self.orphaned_alerts:
                logger.info(f"  {orphan['old_id']} → {orphan['new_id']} (symbol: {orphan['symbol']})")
        
        logger.info("-" * 70)
    
    def _print_final_summary(self):
        """Print final migration summary."""
        logger.info("\nFINAL SUMMARY")
        logger.info("-" * 70)
        logger.info(f"Total documents migrated:  {self.stats['activities_after']}")
        logger.info(f"Alerts merged:             {self.stats['alerts_merged']}")
        logger.info(f"Orphaned alerts:           {self.stats['alerts_orphaned']}")
        logger.info(f"ID collisions resolved:    {self.stats['id_collisions']}")
        logger.info("-" * 70)
    
    def restore_from_backup(self, backup_file: str):
        """Restore database from backup file."""
        logger.info("=" * 70)
        logger.info("RESTORE FROM BACKUP")
        logger.info("=" * 70)
        logger.info(f"Backup file: {backup_file}")
        
        if not os.path.exists(backup_file):
            raise MigrationError(f"Backup file not found: {backup_file}")
        
        # Load backup
        logger.info("Loading backup file...")
        with open(backup_file, 'r') as f:
            backup_data = json.load(f)
        
        activities = backup_data['documents']['activities']
        alerts = backup_data['documents']['alerts']
        
        logger.info(f"Backup contains {len(activities)} activities and {len(alerts)} alerts")
        logger.info(f"Backup timestamp: {backup_data['timestamp']}")
        
        # Confirmation prompt
        logger.warning("⚠️  This will DELETE all current activity/alert documents")
        logger.warning("⚠️  and restore from backup. This action cannot be undone!")
        response = input("Type 'YES' to proceed with restore: ")
        if response != 'YES':
            logger.info("Restore cancelled")
            return
        
        # Delete current documents
        logger.info("Deleting current activity/alert documents...")
        delete_query = "SELECT c.id, c.symbol FROM c WHERE c.doc_type IN ('activity', 'alert')"
        current_docs = list(self.container.query_items(
            query=delete_query,
            enable_cross_partition_query=True
        ))
        
        delete_count = 0
        for doc in current_docs:
            try:
                self.container.delete_item(doc['id'], partition_key=doc['symbol'])
                delete_count += 1
            except Exception as e:
                logger.error(f"Failed to delete {doc['id']}: {e}")
        
        logger.info(f"Deleted {delete_count} documents")
        
        # Restore activities
        logger.info(f"Restoring {len(activities)} activities...")
        write_count = 0
        for doc in activities:
            try:
                self.container.create_item(doc)
                write_count += 1
            except Exception as e:
                logger.error(f"Failed to restore activity {doc['id']}: {e}")
        
        logger.info(f"Restored {write_count} activities")
        
        # Restore alerts
        logger.info(f"Restoring {len(alerts)} alerts...")
        write_count = 0
        for doc in alerts:
            try:
                self.container.create_item(doc)
                write_count += 1
            except Exception as e:
                logger.error(f"Failed to restore alert {doc['id']}: {e}")
        
        logger.info(f"Restored {write_count} alerts")
        logger.info("=" * 70)
        logger.info("✅ RESTORE COMPLETE")
        logger.info("=" * 70)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='CosmosDB Unified Container Migration',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry-run (phases 1-2 only, no writes)
  python scripts/migrate_cosmos_events.py --dry-run
  
  # Execute migration
  python scripts/migrate_cosmos_events.py
  
  # Restore from backup
  python scripts/migrate_cosmos_events.py --restore backups/backup_20260401T1430.json
        """
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Run phases 1-2 only (export + transform), skip writes'
    )
    parser.add_argument(
        '--restore',
        type=str,
        metavar='BACKUP_FILE',
        help='Restore from backup file (deletes current data)'
    )
    
    args = parser.parse_args()
    
    # Load CosmosDB credentials from environment
    endpoint = os.getenv('COSMOS_ENDPOINT')
    key = os.getenv('COSMOS_KEY')
    database_name = os.getenv('COSMOS_DATABASE', 'stock-options-manager')
    
    if not endpoint or not key:
        logger.error("Missing required environment variables:")
        logger.error("  COSMOS_ENDPOINT - CosmosDB endpoint URL")
        logger.error("  COSMOS_KEY - CosmosDB access key")
        sys.exit(1)
    
    try:
        migration = CosmosEventMigration(endpoint, key, database_name)
        
        if args.restore:
            migration.restore_from_backup(args.restore)
        else:
            migration.run_migration(dry_run=args.dry_run)
        
        sys.exit(0)
        
    except MigrationError as e:
        logger.error(f"Migration error: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        logger.warning("\nMigration interrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
