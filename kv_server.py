import socket
import threading
import json
import time
import os
from datetime import datetime, timedelta

EXCHANGE_FORMAT = "utf-8"
BUFFER_SIZE = 4096
PERSISTENCE_INTERVAL = 10  # Save to disk every 10 seconds

class KeyValueServer:
    def __init__(self, server_id, port):
        self.server_id = server_id
        self.port = port
        self.device_name = socket.gethostname()
        self.server_ip = socket.gethostbyname(self.device_name)
        
        # In-memory key-value store
        self.store = {}
        
        # Key expiration tracking: key -> expiration_timestamp
        self.expirations = {}
        
        # Statistics
        self.stats = {
            "total_requests": 0,
            "get_requests": 0,
            "set_requests": 0,
            "delete_requests": 0,
            "expire_requests": 0
        }
        
        # Persistence file
        self.persistence_file = f"kv_store_server{server_id}.json"
        
        # Locks for thread safety
        self.store_lock = threading.Lock()
        self.stats_lock = threading.Lock()
        
        # Load persisted data
        self.load_from_disk()
        
    def start(self):
        """Start the KV server"""
        try:
            server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_socket.bind((self.server_ip, self.port))
            server_socket.listen(10)
            
            print(f"{'='*70}")
            print(f"{'KEY-VALUE SERVER (MINI REDIS)':^70}")
            print(f"{'='*70}")
            print(f"Server ID: {self.server_id}")
            print(f"IP Address: {self.server_ip}")
            print(f"Port: {self.port}")
            print(f"Persistence File: {self.persistence_file}")
            print(f"Persistence Interval: {PERSISTENCE_INTERVAL} seconds")
            print(f"Loaded Keys: {len(self.store)}")
            print(f"{'='*70}\n")
            
            # Start background threads
            persist_thread = threading.Thread(target=self.persist_periodically)
            persist_thread.daemon = True
            persist_thread.start()
            
            expire_thread = threading.Thread(target=self.cleanup_expired_keys)
            expire_thread.daemon = True
            expire_thread.start()
            
            while True:
                conn, addr = server_socket.accept()
                thread = threading.Thread(target=self.handle_client, args=(conn, addr))
                thread.daemon = True
                thread.start()
        
        except Exception as e:
            print(f"[ERROR] Server error: {e}")
        finally:
            self.save_to_disk()
            server_socket.close()
    
    def handle_client(self, conn, addr):
        """Handle client connection"""
        try:
            data = conn.recv(BUFFER_SIZE)
            if not data:
                conn.close()
                return
            
            # Parse request
            request = json.loads(data.decode(EXCHANGE_FORMAT))
            command = request.get("command", "").upper()
            
            timestamp = datetime.now().strftime("%H:%M:%S")
            
            with self.stats_lock:
                self.stats["total_requests"] += 1
            
            # Execute command
            if command == "GET":
                response = self.handle_get(request)
                with self.stats_lock:
                    self.stats["get_requests"] += 1
            
            elif command == "SET":
                response = self.handle_set(request)
                with self.stats_lock:
                    self.stats["set_requests"] += 1
            
            elif command == "DELETE":
                response = self.handle_delete(request)
                with self.stats_lock:
                    self.stats["delete_requests"] += 1
            
            elif command == "EXPIRE":
                response = self.handle_expire(request)
                with self.stats_lock:
                    self.stats["expire_requests"] += 1
            
            elif command == "STATS":
                response = self.handle_stats()
            
            elif command == "KEYS":
                response = self.handle_keys()
            
            else:
                response = {
                    "status": "error",
                    "message": f"Unknown command: {command}"
                }
            
            # Add server info to response
            response["server_id"] = self.server_id
            response["server_port"] = self.port
            
            # Log request
            print(f"[{timestamp}] [{command}] {addr[0]} - Key: {request.get('key', 'N/A')}")
            
            # Send response
            conn.send(json.dumps(response).encode(EXCHANGE_FORMAT))
        
        except json.JSONDecodeError:
            error = {"status": "error", "message": "Invalid JSON"}
            conn.send(json.dumps(error).encode(EXCHANGE_FORMAT))
        except Exception as e:
            error = {"status": "error", "message": str(e)}
            conn.send(json.dumps(error).encode(EXCHANGE_FORMAT))
        finally:
            conn.close()
    
    def handle_get(self, request):
        """Handle GET command"""
        key = request.get("key")
        
        if not key:
            return {"status": "error", "message": "Key is required"}
        
        with self.store_lock:
            # Check if key exists and not expired
            if key in self.store:
                if self.is_expired(key):
                    del self.store[key]
                    if key in self.expirations:
                        del self.expirations[key]
                    return {"status": "null", "message": "Key expired"}
                
                value = self.store[key]
                ttl = self.get_ttl(key)
                return {
                    "status": "success",
                    "key": key,
                    "value": value,
                    "ttl": ttl
                }
            else:
                return {"status": "null", "message": "Key not found"}
    
    def handle_set(self, request):
        """Handle SET command"""
        key = request.get("key")
        value = request.get("value")
        
        if not key:
            return {"status": "error", "message": "Key is required"}
        
        with self.store_lock:
            self.store[key] = value
            # Remove expiration if it exists
            if key in self.expirations:
                del self.expirations[key]
        
        return {
            "status": "success",
            "message": f"Key '{key}' set successfully",
            "key": key,
            "value": value
        }
    
    def handle_delete(self, request):
        """Handle DELETE command"""
        key = request.get("key")
        
        if not key:
            return {"status": "error", "message": "Key is required"}
        
        with self.store_lock:
            if key in self.store:
                del self.store[key]
                if key in self.expirations:
                    del self.expirations[key]
                return {
                    "status": "success",
                    "message": f"Key '{key}' deleted successfully"
                }
            else:
                return {"status": "null", "message": "Key not found"}
    
    def handle_expire(self, request):
        """Handle EXPIRE command"""
        key = request.get("key")
        seconds = request.get("seconds")
        
        if not key:
            return {"status": "error", "message": "Key is required"}
        
        if seconds is None:
            return {"status": "error", "message": "Seconds is required"}
        
        try:
            seconds = int(seconds)
        except ValueError:
            return {"status": "error", "message": "Seconds must be an integer"}
        
        with self.store_lock:
            if key not in self.store:
                return {"status": "null", "message": "Key not found"}
            
            expiration_time = datetime.now() + timedelta(seconds=seconds)
            self.expirations[key] = expiration_time.timestamp()
            
            return {
                "status": "success",
                "message": f"Key '{key}' will expire in {seconds} seconds",
                "key": key,
                "ttl": seconds
            }
    
    def handle_stats(self):
        """Handle STATS command"""
        with self.stats_lock:
            stats_copy = self.stats.copy()
        
        with self.store_lock:
            stats_copy["total_keys"] = len(self.store)
            stats_copy["keys_with_ttl"] = len(self.expirations)
        
        return {
            "status": "success",
            "stats": stats_copy
        }
    
    def handle_keys(self):
        """Handle KEYS command (list all keys)"""
        with self.store_lock:
            keys = list(self.store.keys())
        
        return {
            "status": "success",
            "keys": keys,
            "count": len(keys)
        }
    
    def is_expired(self, key):
        """Check if key is expired"""
        if key not in self.expirations:
            return False
        
        return datetime.now().timestamp() > self.expirations[key]
    
    def get_ttl(self, key):
        """Get time-to-live for key in seconds"""
        if key not in self.expirations:
            return -1  # No expiration
        
        ttl = self.expirations[key] - datetime.now().timestamp()
        return max(0, int(ttl))
    
    def cleanup_expired_keys(self):
        """Background thread to remove expired keys"""
        while True:
            time.sleep(5)  # Check every 5 seconds
            
            with self.store_lock:
                expired_keys = []
                for key in list(self.expirations.keys()):
                    if self.is_expired(key):
                        expired_keys.append(key)
                
                for key in expired_keys:
                    del self.store[key]
                    del self.expirations[key]
                
                if expired_keys:
                    timestamp = datetime.now().strftime("%H:%M:%S")
                    print(f"[{timestamp}] [CLEANUP] Removed {len(expired_keys)} expired key(s)")
    
    def persist_periodically(self):
        """Background thread to save data to disk"""
        while True:
            time.sleep(PERSISTENCE_INTERVAL)
            self.save_to_disk()
    
    def save_to_disk(self):
        """Save data to disk"""
        try:
            with self.store_lock:
                data = {
                    "store": self.store,
                    "expirations": self.expirations,
                    "stats": self.stats
                }
            
            with open(self.persistence_file, 'w') as f:
                json.dump(data, f, indent=2)
            
            timestamp = datetime.now().strftime("%H:%M:%S")
            print(f"[{timestamp}] [PERSIST] Saved {len(self.store)} key(s) to disk")
        
        except Exception as e:
            print(f"[ERROR] Failed to save to disk: {e}")
    
    def load_from_disk(self):
        """Load data from disk"""
        if not os.path.exists(self.persistence_file):
            print(f"[INFO] No persistence file found, starting fresh")
            return
        
        try:
            with open(self.persistence_file, 'r') as f:
                data = json.load(f)
            
            self.store = data.get("store", {})
            self.expirations = data.get("expirations", {})
            self.stats = data.get("stats", self.stats)
            
            # Clean up expired keys on load
            with self.store_lock:
                expired_keys = []
                for key in list(self.expirations.keys()):
                    if self.is_expired(key):
                        expired_keys.append(key)
                
                for key in expired_keys:
                    del self.store[key]
                    del self.expirations[key]
            
            print(f"[INFO] Loaded {len(self.store)} key(s) from disk")
            if expired_keys:
                print(f"[INFO] Removed {len(expired_keys)} expired key(s)")
        
        except Exception as e:
            print(f"[ERROR] Failed to load from disk: {e}")

def main():
    """Main function"""
    import sys
    
    if len(sys.argv) != 3:
        print("Usage: python kv_server.py <server_id> <port>")
        print("Example: python kv_server.py 1 7001")
        print("\nRecommended setup for sharding:")
        print("  Server 1: python kv_server.py 1 7001")
        print("  Server 2: python kv_server.py 2 7002")
        print("  Server 3: python kv_server.py 3 7003")
        sys.exit(1)
    
    try:
        server_id = int(sys.argv[1])
        port = int(sys.argv[2])
        
        server = KeyValueServer(server_id, port)
        server.start()
    
    except KeyboardInterrupt:
        print("\n\n[SERVER] Shutting down...")
        print("[SERVER] Server stopped")
    except Exception as e:
        print(f"[ERROR] {e}")

if __name__ == "__main__":
    main()
