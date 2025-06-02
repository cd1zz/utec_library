#!/usr/bin/env python3
"""
Generic U-tec lock testing and control script.
Follows KISS, YAGNI, and SOLID principles.
"""

import asyncio
import logging
import sys
import os
from typing import List, Optional
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import utec

# Configure logging
logging.basicConfig(
    level=logging.INFO,  # Less verbose for cleaner output
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('lock_test.log')
    ]
)
logger = logging.getLogger(__name__)


class LockTester:
    """Generic lock tester for U-tec devices."""
    
    def __init__(self, utec_email: str, utec_password: str):
        """Initialize the lock tester."""
        self.utec_email = utec_email
        self.utec_password = utec_password
        self.locks: List = []
        
    async def test_login_and_discovery(self) -> bool:
        """Test login and discover all available locks."""
        try:
            print("\n" + "="*60)
            print("TESTING U-TEC LOGIN AND DEVICE DISCOVERY")
            print("="*60)
            
            logger.info("Initializing U-tec library...")
            utec.setup(log_level=utec.LogLevel.INFO)
            
            print("Discovering U-tec devices...")
            self.locks = await utec.discover_devices(self.utec_email, self.utec_password)
            
            if not self.locks:
                print("ERROR: No U-tec devices found")
                print("   - Check your credentials in .env file")
                print("   - Ensure devices are registered to your account")
                return False
            
            print(f"SUCCESS: Found {len(self.locks)} device(s)")
            print("\nDISCOVERED LOCKS:")
            print("-" * 60)
            
            for i, lock in enumerate(self.locks, 1):
                print(f"{i:2d}. {lock.name}")
                print(f"     MAC: {lock.mac_uuid}")
                print(f"     Model: {lock.model}")
                print(f"     UID: {lock.uid}")
                print(f"     Serial: {getattr(lock, 'sn', 'Unknown')}")
                
                # Show capabilities
                caps = []
                if getattr(lock.capabilities, 'bluetooth', False): caps.append('BLE')
                if getattr(lock.capabilities, 'autolock', False): caps.append('AutoLock')
                if getattr(lock.capabilities, 'bt264', False): caps.append('BT264')
                if getattr(lock.capabilities, 'keypad', False): caps.append('Keypad')
                if getattr(lock.capabilities, 'fingprinter', False): caps.append('Fingerprint')
                
                print(f"     Features: {', '.join(caps) if caps else 'Basic'}")
                print()
            
            return True
            
        except Exception as e:
            logger.error(f"Login/discovery failed: {e}", exc_info=True)
            print(f"ERROR: Discovery failed: {e}")
            return False
    
    def select_lock(self) -> Optional[object]:
        """Allow user to select a lock for testing."""
        if not self.locks:
            print("ERROR: No locks available")
            return None
        
        if len(self.locks) == 1:
            print(f"Auto-selecting only lock: {self.locks[0].name}")
            return self.locks[0]
        
        print("\n" + "="*50)
        print("SELECT LOCK FOR TESTING")
        print("="*50)
        
        for i, lock in enumerate(self.locks, 1):
            print(f"{i}. {lock.name} ({lock.model})")
        
        while True:
            try:
                choice = input(f"\nEnter lock number (1-{len(self.locks)}): ").strip()
                
                if choice.lower() in ['q', 'quit', 'exit']:
                    return None
                
                lock_num = int(choice)
                if 1 <= lock_num <= len(self.locks):
                    selected = self.locks[lock_num - 1]
                    print(f"Selected: {selected.name}")
                    return selected
                else:
                    print(f"ERROR: Please enter a number between 1 and {len(self.locks)}")
                    
            except ValueError:
                print("ERROR: Please enter a valid number")
            except KeyboardInterrupt:
                print("\nCancelled")
                return None
    
    async def get_lock_status(self, lock) -> dict:
        """Get comprehensive lock status with interpretation."""
        try:
            print(f"\nGetting status for {lock.name}...")
            await lock.async_update_status()
            
            # Debug: Show raw values from device
            print(f"Raw values - Lock: {lock.lock_status}, Bolt: {lock.bolt_status}, Battery: {lock.battery}, Mode: {lock.lock_mode}")
            
            # Interpret status values
            lock_status_text = {
                0: "Unknown/Error",
                1: "Unlocked", 
                2: "Locked",
                255: "Not Available"
            }.get(lock.lock_status, f"Unknown ({lock.lock_status})")
            
            bolt_status_text = {
                0: "Retracted (Unlocked)",
                1: "Extended (Locked)", 
                255: "No Bolt/Jammed"
            }.get(lock.bolt_status, f"Unknown ({lock.bolt_status})")
            
            battery_text = {
                0: "Critical (Replace)",
                1: "Low", 
                2: "Medium",
                3: "High",
                -1: "Unknown"
            }.get(lock.battery, f"Unknown ({lock.battery})")
            
            mode_text = {
                0: "Normal Mode",
                1: "Passage Mode", 
                2: "Lockout Mode"
            }.get(lock.lock_mode, f"Unknown ({lock.lock_mode})")
            
            # Check for potential issues
            issues = []
            if lock.bolt_status == 255:
                issues.append("WARNING: BOLT JAMMED OR NOT AVAILABLE")
            if lock.battery <= 1:
                issues.append("WARNING: LOW BATTERY - Replace soon")
            if lock.lock_status == 0:
                issues.append("WARNING: Unknown lock state")
            
            status_info = {
                'name': lock.name,
                'model': lock.model,
                'mac': lock.mac_uuid,
                'lock_status': lock.lock_status,
                'lock_status_text': lock_status_text,
                'bolt_status': lock.bolt_status,
                'bolt_status_text': bolt_status_text,
                'battery': lock.battery,
                'battery_text': battery_text,
                'mode': lock.lock_mode,
                'mode_text': mode_text,
                'mute': lock.mute,
                'serial': getattr(lock, 'sn', 'Unknown'),
                'busy': lock.is_busy,
                'issues': issues
            }
            
            # Display status
            print("\n" + "="*50)
            print("LOCK STATUS")
            print("="*50)
            print(f"Device: {status_info['name']} ({status_info['model']})")
            print(f"MAC: {status_info['mac']}")
            print(f"Lock Status: {status_info['lock_status_text']}")
            print(f"Bolt Status: {status_info['bolt_status_text']}")
            print(f"Battery: {status_info['battery_text']}")
            print(f"Mode: {status_info['mode_text']}")
            print(f"Mute: {'Yes' if status_info['mute'] else 'No'}")
            print(f"Serial: {status_info['serial']}")
            print(f"Busy: {'Yes' if status_info['busy'] else 'No'}")
            
            if issues:
                print(f"\nISSUES DETECTED:")
                for issue in issues:
                    print(f"   {issue}")
            else:
                print(f"\nNo issues detected")
            
            print("="*50)
            
            return status_info
            
        except Exception as e:
            logger.error(f"Failed to get status: {e}", exc_info=True)
            print(f"ERROR: Failed to get status: {e}")
            return {}
    
    async def safe_lock_operation(self, lock, operation: str) -> bool:
        """Perform lock operation with pre/post status checks."""
        try:
            # Pre-operation status check
            print(f"\nChecking status before {operation}...")
            pre_status = await self.get_lock_status(lock)
            
            if not pre_status:
                print(f"ERROR: Cannot {operation} - failed to get current status")
                return False
            
            # Check for bolt jam
            if pre_status.get('bolt_status') == 255:
                print(f"ERROR: Cannot {operation} - bolt appears to be jammed or unavailable")
                print("   Please check the lock mechanism manually")
                return False
            
            # Check if device is busy
            if pre_status.get('busy'):
                print(f"ERROR: Cannot {operation} - device is currently busy")
                return False
            
            # Confirm operation
            current_state = "locked" if pre_status.get('lock_status') == 2 else "unlocked"
            print(f"\nCurrent state: {current_state}")
            
            if operation == "lock" and pre_status.get('lock_status') == 2:
                print("INFO: Lock is already locked")
                return True
            elif operation == "unlock" and pre_status.get('lock_status') == 1:
                print("INFO: Lock is already unlocked") 
                return True
            
            # Perform the operation
            print(f"\nPerforming {operation} operation...")
            
            if operation == "lock":
                await lock.async_lock()
            elif operation == "unlock":
                await lock.async_unlock()
            else:
                print(f"ERROR: Unknown operation: {operation}")
                return False
            
            print(f"SUCCESS: {operation.title()} command sent successfully!")
            
            # Wait for operation to complete
            print("Waiting for operation to complete...")
            await asyncio.sleep(3)
            
            # Post-operation status check
            print(f"Verifying {operation} result...")
            post_status = await self.get_lock_status(lock)
            
            if not post_status:
                print(f"WARNING: {operation.title()} command sent, but cannot verify result")
                return False
            
            # Verify operation success
            expected_status = 2 if operation == "lock" else 1
            if post_status.get('lock_status') == expected_status:
                print(f"SUCCESS: {operation.title()} operation completed successfully!")
                return True
            elif post_status.get('bolt_status') == 255:
                print(f"ERROR: {operation.title()} failed - bolt jam detected after operation")
                return False
            else:
                print(f"WARNING: {operation.title()} command sent, but lock state may not have changed")
                print(f"   Expected status: {expected_status}, Got: {post_status.get('lock_status')}")
                return False
                
        except Exception as e:
            logger.error(f"Lock operation failed: {e}", exc_info=True)
            print(f"ERROR: {operation} failed: {e}")
            return False
    
    async def run_interactive_menu(self):
        """Run the main interactive testing menu."""
        # Test login and discovery
        if not await self.test_login_and_discovery():
            return False
        
        # Select lock to test
        selected_lock = self.select_lock()
        if not selected_lock:
            print("No lock selected, exiting")
            return True
        
        # Interactive menu
        while True:
            print(f"\n{'='*60}")
            print(f"LOCK TESTING MENU - {selected_lock.name}")
            print(f"{'='*60}")
            print("1. Get Lock Status")
            print("2. Lock Device")
            print("3. Unlock Device")
            print("4. Select Different Lock")
            print("5. Exit")
            print("-" * 60)
            
            try:
                choice = input("Enter your choice (1-5): ").strip()
                
                if choice == '1':
                    await self.get_lock_status(selected_lock)
                    
                elif choice == '2':
                    confirm = input("\nWARNING: Are you sure you want to LOCK the device? (y/N): ").strip().lower()
                    if confirm == 'y':
                        await self.safe_lock_operation(selected_lock, "lock")
                    else:
                        print("Lock operation cancelled")
                        
                elif choice == '3':
                    confirm = input("\nWARNING: Are you sure you want to UNLOCK the device? (y/N): ").strip().lower()
                    if confirm == 'y':
                        await self.safe_lock_operation(selected_lock, "unlock")
                    else:
                        print("Unlock operation cancelled")
                        
                elif choice == '4':
                    new_lock = self.select_lock()
                    if new_lock:
                        selected_lock = new_lock
                    
                elif choice == '5':
                    print("Exiting lock tester")
                    break
                    
                else:
                    print("ERROR: Invalid choice. Please enter 1-5.")
                    
            except KeyboardInterrupt:
                print("\nExiting...")
                break
            except Exception as e:
                logger.error(f"Menu error: {e}", exc_info=True)
                print(f"ERROR: {e}")
        
        return True


def load_config():
    """Load configuration from environment variables."""
    utec_email = os.getenv('UTEC_EMAIL')
    utec_password = os.getenv('UTEC_PASSWORD')
    
    if not all([utec_email, utec_password]):
        missing = []
        if not utec_email: missing.append('UTEC_EMAIL')
        if not utec_password: missing.append('UTEC_PASSWORD')
        
        print("ERROR: Missing required environment variables:")
        for var in missing:
            print(f"   - {var}")
        print("\nPlease create a .env file with:")
        print("   UTEC_EMAIL=your@email.com")
        print("   UTEC_PASSWORD=your_password")
        
        raise ValueError(f"Missing required environment variables: {missing}")
    
    return {
        'utec_email': utec_email,
        'utec_password': utec_password
    }


async def main():
    """Main application entry point."""
    try:
        print("U-TEC LOCK TESTER")
        print("=" * 60)
        print("Generic testing tool for U-tec smart locks")
        print("Supports login testing, status checking, and safe lock operations")
        print()
        
        # Load configuration
        config = load_config()
        logger.info("Configuration loaded successfully")
        
        # Create and run tester
        tester = LockTester(**config)
        success = await tester.run_interactive_menu()
        
        return 0 if success else 1
        
    except Exception as e:
        logger.error(f"Application error: {e}", exc_info=True)
        print(f"ERROR: Fatal error: {e}")
        return 1


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\nApplication interrupted by user")
        sys.exit(0)