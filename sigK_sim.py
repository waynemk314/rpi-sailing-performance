import asyncio
import websockets
import json
import math

# Helper to convert Radians to Degrees
def rad_to_deg(radians):
    if radians is None: return 0.0
    return radians * (180 / math.pi)

# Helper to convert Meters/Second to Knots
def ms_to_knots(ms):
    if ms is None: return 0.0
    return ms * 1.94384

async def monitor_sailing_data():
    # Connect to Signal K WebSocket
    # Ensure your Signal K server is running on localhost:3000
    uri = "ws://localhost:3000/signalk/v1/stream?subscribe=none"
    
    try:
        async with websockets.connect(uri) as websocket:
            print("Connected to Signal K. Waiting for data...")
            
            # Subscribe to the specific paths needed for Polars
            subscribe_msg = {
                "context": "vessels.self",
                "subscribe": [
                    {"path": "navigation.speedThroughWater", "period": 1000},     # STW
                    {"path": "environment.wind.angleApparent", "period": 1000},   # AWA
                    {"path": "environment.wind.speedApparent", "period": 1000},   # AWS
                    {"path": "environment.wind.angleTrueWater", "period": 1000},  # TWA
                    {"path": "environment.wind.speedTrue", "period": 1000}        # TWS
                ]
            }
            await websocket.send(json.dumps(subscribe_msg))

            # Dictionary to hold latest values so we can print them together
            current_data = {
                "STW": 0.0,
                "AWA": 0.0,
                "AWS": 0.0,
                "TWA": 0.0,
                "TWS": 0.0
            }

            while True:
                message = await websocket.recv()
                data = json.loads(message)
                
                if 'updates' in data:
                    for update in data['updates']:
                        for value in update['values']:
                            path = value['path']
                            val = value['value']
                            
                            # Update our local state with converted units
                            if path == "navigation.speedThroughWater":
                                current_data["STW"] = ms_to_knots(val)
                            
                            elif path == "environment.wind.angleApparent":
                                current_data["AWA"] = rad_to_deg(val)
                                
                            elif path == "environment.wind.speedApparent":
                                current_data["AWS"] = ms_to_knots(val)
                                
                            elif path == "environment.wind.angleTrueWater":
                                current_data["TWA"] = rad_to_deg(val)
                                
                            elif path == "environment.wind.speedTrue":
                                current_data["TWS"] = ms_to_knots(val)
                    
                    # Print formatted output
                    print(f"STW: {current_data['STW']:.1f} kts | "
                          f"AWA: {current_data['AWA']:.1f}° | "
                          f"AWS: {current_data['AWS']:.1f} kts | "
                          f"TWA: {current_data['TWA']:.1f}° | "
                          f"TWS: {current_data['TWS']:.1f} kts")

    except Exception as e:
        print(f"Connection Error: {e}")
        print("Make sure Signal K is running and the 'File Stream' connection is active.")

if __name__ == "__main__":
    asyncio.run(monitor_sailing_data())