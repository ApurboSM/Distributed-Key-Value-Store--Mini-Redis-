import socket
import json
import hashlib

EXCHANGE_FORMAT = "utf-8"
BUFFER_SIZE = 4096

# Server pool configuration
SERVERS = [
    {"id": 1, "host": "localhost", "port": 7001},
    {"id": 2, "host": "localhost", "port": 7002},
    {"id": 3, "host": "localhost", "port": 7003}
]

# Retry configuration
MAX_RETRIES = 3
RETRY_DELAY = 0.5  # seconds

class DistributedKVClient:
    def __init__(self, use_sharding=True):
        self.use_sharding = use_sharding
        self.device_name = socket.gethostname()
        
    def get_server_for_key(self, key):
        """Determine which server should handle this key using consistent hashing"""
        if not self.use_sharding:
            # Use first server if sharding disabled
            return SERVERS[0]
        
        # Hash the key and mod by number of servers
        key_hash = int(hashlib.md5(key.encode()).hexdigest(), 16)
        server_index = key_hash % len(SERVERS)
        return SERVERS[server_index]
    
    def send_request(self, server, request, retry_count=0):
        """Send request to server with retry logic"""
        try:
            # Connect to server
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            sock.connect((server["host"], server["port"]))
            
            # Send request
            sock.send(json.dumps(request).encode(EXCHANGE_FORMAT))
            
            # Receive response
            response_data = sock.recv(BUFFER_SIZE)
            response = json.loads(response_data.decode(EXCHANGE_FORMAT))
            
            sock.close()
            return response
        
        except (socket.timeout, ConnectionRefusedError, socket.error) as e:
            # Retry logic
            if retry_count < MAX_RETRIES:
                print(f"[RETRY] Server {server['id']} failed, retrying... ({retry_count + 1}/{MAX_RETRIES})")
                import time
                time.sleep(RETRY_DELAY)
                return self.send_request(server, request, retry_count + 1)
            else:
                return {
                    "status": "error",
                    "message": f"Server {server['id']} unavailable after {MAX_RETRIES} retries",
                    "server_id": server['id']
                }
        
        except json.JSONDecodeError:
            return {
                "status": "error",
                "message": "Invalid response from server",
                "server_id": server['id']
            }
        except Exception as e:
            return {
                "status": "error",
                "message": str(e),
                "server_id": server.get('id', 'unknown')
            }
    
    def get(self, key):
        """GET key"""
        server = self.get_server_for_key(key)
        request = {
            "command": "GET",
            "key": key
        }
        return self.send_request(server, request)
    
    def set(self, key, value):
        """SET key value"""
        server = self.get_server_for_key(key)
        request = {
            "command": "SET",
            "key": key,
            "value": value
        }
        return self.send_request(server, request)
    
    def delete(self, key):
        """DELETE key"""
        server = self.get_server_for_key(key)
        request = {
            "command": "DELETE",
            "key": key
        }
        return self.send_request(server, request)
    
    def expire(self, key, seconds):
        """EXPIRE key seconds"""
        server = self.get_server_for_key(key)
        request = {
            "command": "EXPIRE",
            "key": key,
            "seconds": seconds
        }
        return self.send_request(server, request)
    
    def stats(self, server_id=None):
        """Get statistics from server(s)"""
        if server_id is not None:
            # Get stats from specific server
            server = next((s for s in SERVERS if s['id'] == server_id), None)
            if not server:
                return {"status": "error", "message": f"Server {server_id} not found"}
            
            request = {"command": "STATS"}
            return self.send_request(server, request)
        else:
            # Get stats from all servers
            all_stats = []
            for server in SERVERS:
                request = {"command": "STATS"}
                response = self.send_request(server, request)
                all_stats.append({
                    "server_id": server['id'],
                    "port": server['port'],
                    "response": response
                })
            return all_stats
    
    def keys(self, server_id=None):
        """List keys on server(s)"""
        if server_id is not None:
            # Get keys from specific server
            server = next((s for s in SERVERS if s['id'] == server_id), None)
            if not server:
                return {"status": "error", "message": f"Server {server_id} not found"}
            
            request = {"command": "KEYS"}
            return self.send_request(server, request)
        else:
            # Get keys from all servers
            all_keys = []
            for server in SERVERS:
                request = {"command": "KEYS"}
                response = self.send_request(server, request)
                all_keys.append({
                    "server_id": server['id'],
                    "port": server['port'],
                    "response": response
                })
            return all_keys

def print_response(response):
    """Pretty print response"""
    if isinstance(response, list):
        # Multiple responses (from all servers)
        for item in response:
            print(f"\n{'='*60}")
            print(f"Server {item['server_id']} (Port {item['port']})")
            print(f"{'='*60}")
            print_single_response(item['response'])
    else:
        print_single_response(response)

def print_single_response(response):
    """Print single response"""
    status = response.get("status", "unknown")
    server_id = response.get("server_id", "N/A")
    
    if status == "success":
        print(f"✓ SUCCESS (Server {server_id})")
        
        if "value" in response:
            print(f"  Key: {response.get('key')}")
            print(f"  Value: {response.get('value')}")
            ttl = response.get('ttl', -1)
            if ttl >= 0:
                print(f"  TTL: {ttl} seconds")
            else:
                print(f"  TTL: No expiration")
        
        if "stats" in response:
            stats = response["stats"]
            print(f"  Total Requests: {stats.get('total_requests', 0)}")
            print(f"  GET: {stats.get('get_requests', 0)}")
            print(f"  SET: {stats.get('set_requests', 0)}")
            print(f"  DELETE: {stats.get('delete_requests', 0)}")
            print(f"  EXPIRE: {stats.get('expire_requests', 0)}")
            print(f"  Total Keys: {stats.get('total_keys', 0)}")
            print(f"  Keys with TTL: {stats.get('keys_with_ttl', 0)}")
        
        if "keys" in response:
            keys = response["keys"]
            print(f"  Keys ({response.get('count', 0)}): {', '.join(keys) if keys else 'None'}")
        
        if "message" in response:
            print(f"  {response['message']}")
    
    elif status == "null":
        print(f"⊘ NOT FOUND (Server {server_id})")
        print(f"  {response.get('message', 'Key not found')}")
    
    elif status == "error":
        print(f"✗ ERROR (Server {server_id})")
        print(f"  {response.get('message', 'Unknown error')}")
    
    else:
        print(f"? UNKNOWN STATUS: {status}")
        print(f"  Response: {response}")

def show_help():
    """Show help message"""
    print(f"\n{'='*70}")
    print("DISTRIBUTED KEY-VALUE STORE CLIENT")
    print(f"{'='*70}")
    print("\nCommands:")
    print("  set <key> <value>        - Set key to value")
    print("  get <key>                - Get value of key")
    print("  del <key>                - Delete key")
    print("  expire <key> <seconds>   - Set expiration for key")
    print("  stats [server_id]        - Show statistics (all or specific server)")
    print("  keys [server_id]         - List keys (all or specific server)")
    print("  servers                  - Show server configuration")
    print("  help                     - Show this help")
    print("  exit                     - Exit client")
    print(f"{'='*70}")
    print("\nExamples:")
    print("  set user:1 John")
    print("  get user:1")
    print("  expire user:1 60")
    print("  del user:1")
    print("  stats")
    print("  keys 1")
    print(f"{'='*70}\n")

def show_servers():
    """Show server configuration"""
    print(f"\n{'='*70}")
    print("SERVER CONFIGURATION")
    print(f"{'='*70}")
    for server in SERVERS:
        print(f"Server {server['id']}: {server['host']}:{server['port']}")
    print(f"Total Servers: {len(SERVERS)}")
    print(f"Sharding: Enabled (Consistent Hashing)")
    print(f"Retry Policy: {MAX_RETRIES} retries with {RETRY_DELAY}s delay")
    print(f"{'='*70}\n")

def main():
    """Main interactive client"""
    client = DistributedKVClient(use_sharding=True)
    
    print(f"{'='*70}")
    print(f"{'DISTRIBUTED KEY-VALUE STORE CLIENT':^70}")
    print(f"{'='*70}")
    print(f"Servers: {len(SERVERS)} nodes")
    print(f"Sharding: Enabled (MD5 Consistent Hashing)")
    print(f"Fault Tolerance: {MAX_RETRIES} retries")
    print(f"{'='*70}")
    
    show_help()
    
    while True:
        try:
            user_input = input("kv> ").strip()
            
            if not user_input:
                continue
            
            parts = user_input.split(maxsplit=2)
            command = parts[0].lower()
            
            if command == "exit" or command == "quit":
                print("\nGoodbye!\n")
                break
            
            elif command == "help":
                show_help()
            
            elif command == "servers":
                show_servers()
            
            elif command == "set":
                if len(parts) < 3:
                    print("Usage: set <key> <value>")
                else:
                    key = parts[1]
                    value = parts[2]
                    response = client.set(key, value)
                    print_response(response)
            
            elif command == "get":
                if len(parts) < 2:
                    print("Usage: get <key>")
                else:
                    key = parts[1]
                    response = client.get(key)
                    print_response(response)
            
            elif command == "del" or command == "delete":
                if len(parts) < 2:
                    print("Usage: del <key>")
                else:
                    key = parts[1]
                    response = client.delete(key)
                    print_response(response)
            
            elif command == "expire":
                if len(parts) < 3:
                    print("Usage: expire <key> <seconds>")
                else:
                    key = parts[1]
                    try:
                        seconds = int(parts[2])
                        response = client.expire(key, seconds)
                        print_response(response)
                    except ValueError:
                        print("Error: seconds must be an integer")
            
            elif command == "stats":
                if len(parts) >= 2:
                    try:
                        server_id = int(parts[1])
                        response = client.stats(server_id)
                    except ValueError:
                        print("Error: server_id must be an integer")
                        continue
                else:
                    response = client.stats()
                
                print_response(response)
            
            elif command == "keys":
                if len(parts) >= 2:
                    try:
                        server_id = int(parts[1])
                        response = client.keys(server_id)
                    except ValueError:
                        print("Error: server_id must be an integer")
                        continue
                else:
                    response = client.keys()
                
                print_response(response)
            
            else:
                print(f"Unknown command: {command}")
                print("Type 'help' for available commands")
        
        except KeyboardInterrupt:
            print("\n\nGoodbye!\n")
            break
        except Exception as e:
            print(f"\nError: {e}\n")

if __name__ == "__main__":
    main()
