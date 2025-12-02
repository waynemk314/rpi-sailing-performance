#!/usr/bin/env python3
"""
Sailing Efficiency Monitor
Reads wind data from Signal K, calculates polar performance efficiency,
and transmits the result as Engine Load % on NMEA 2000 via Yacht Devices YDNU-02.
"""

import asyncio
import websockets
import json
import math
import serial
import struct
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional

# Import the polar model
from polars import BoatPerformance


@dataclass
class SailingData:
    """Container for current sailing telemetry"""
    stw: float = 0.0   # Speed Through Water (knots)
    twa: float = 0.0   # True Wind Angle (degrees)
    tws: float = 0.0   # True Wind Speed (knots)
    timestamp: float = 0.0


class NMEA2000Gateway:
    """
    Interface to Yacht Devices YDNU-02 USB Gateway in RAW mode.
    Sends PGN 127489 (Engine Parameters, Dynamic) with efficiency as Engine Load %.
    """
    
    PGN_ENGINE_DYNAMIC = 127489
    
    def __init__(self, port: str = '/dev/ttyACM0', source_address: int = 42):
        """
        Args:
            port: Serial port for the YDNU-02 gateway
            source_address: NMEA 2000 source address for this device (0-252)
        """
        self.port = port
        self.source_address = source_address
        self.serial = None
        self.sequence_counter = 0  # For fast-packet framing
        
    def connect(self):
        """Open serial connection to the gateway"""
        try:
            self.serial = serial.Serial(
                port=self.port,
                baudrate=115200,
                timeout=1
            )
            # Switch to RAW mode for direct N2K message transmission
            time.sleep(0.5)
            self.serial.write(b'YDNU MODE RAW\r\n')
            time.sleep(0.5)
            # Clear any response
            self.serial.read(self.serial.in_waiting)
            print(f"Connected to NMEA 2000 gateway on {self.port}")
            return True
        except Exception as e:
            print(f"Failed to connect to gateway: {e}")
            return False
    
    def disconnect(self):
        """Close serial connection"""
        if self.serial and self.serial.is_open:
            self.serial.close()
    
    def _build_pgn_127489(self, engine_load_percent: float, engine_instance: int = 0) -> bytes:
        """
        Build PGN 127489 Engine Parameters, Dynamic payload.
        
        Field layout (26 bytes total):
        - Byte 0: Engine Instance (uint8)
        - Bytes 1-2: Oil Pressure (uint16, 100 Pa resolution) - 0xFFFF = N/A
        - Bytes 3-4: Oil Temperature (uint16, 0.1K resolution) - 0xFFFF = N/A
        - Bytes 5-6: Engine Temperature (uint16, 0.1K resolution) - 0xFFFF = N/A
        - Bytes 7-8: Alternator Potential (uint16, 0.01V resolution) - 0xFFFF = N/A
        - Bytes 9-10: Fuel Rate (uint16, 0.1 L/h resolution, signed) - 0x7FFF = N/A
        - Bytes 11-14: Total Engine Hours (uint32, 1s resolution) - 0xFFFFFFFF = N/A
        - Bytes 15-16: Coolant Pressure (uint16, 100 Pa resolution) - 0xFFFF = N/A
        - Bytes 17-18: Fuel Pressure (uint16, 1000 Pa resolution) - 0xFFFF = N/A
        - Bytes 19-22: Reserved (4 bytes, 0xFF)
        - Byte 23: Discrete Status 1 (bit field) - 0x00
        - Byte 24: Engine Load % (uint8, 1% resolution, 0-250 = 0-250%)
        - Byte 25: Engine Torque % (int8, 1% resolution, signed)
        """
        # Clamp efficiency to valid range (0-125% mapped to 0-125)
        load_value = int(min(max(engine_load_percent, 0), 125))
        
        data = bytearray(26)
        
        # Engine Instance
        data[0] = engine_instance
        
        # Set all optional fields to N/A (0xFF or 0xFFFF)
        data[1:3] = struct.pack('<H', 0xFFFF)   # Oil Pressure
        data[3:5] = struct.pack('<H', 0xFFFF)   # Oil Temperature  
        data[5:7] = struct.pack('<H', 0xFFFF)   # Engine Temperature
        data[7:9] = struct.pack('<H', 0xFFFF)   # Alternator Potential
        data[9:11] = struct.pack('<h', 0x7FFF)  # Fuel Rate (signed)
        data[11:15] = struct.pack('<I', 0xFFFFFFFF)  # Total Engine Hours
        data[15:17] = struct.pack('<H', 0xFFFF) # Coolant Pressure
        data[17:19] = struct.pack('<H', 0xFFFF) # Fuel Pressure
        data[19:23] = bytes([0xFF, 0xFF, 0xFF, 0xFF])  # Reserved
        data[23] = 0x00  # Discrete Status 1 (no warnings)
        data[24] = load_value  # Engine Load %
        data[25] = 0x7F  # Engine Torque % (N/A for signed byte)
        
        return bytes(data)
    
    def _build_fast_packet_frames(self, pgn: int, data: bytes, priority: int = 6) -> list:
        """
        Build fast-packet frames for multi-frame PGN transmission.
        
        Fast-packet framing:
        - Frame 0: [seq_counter<<5 | 0] [total_bytes] [data0-5]
        - Frame 1+: [seq_counter<<5 | frame_num] [data...]
        
        Returns list of (can_id, frame_data) tuples for RAW mode transmission.
        """
        frames = []
        total_bytes = len(data)
        
        # Build CAN ID: Priority(3) + Reserved(1) + DP(1) + PF(8) + PS(8) + SA(8)
        # PGN 127489 = 0x1F201 -> PF=0xF2, PS=0x01, DP=1
        # For broadcast: destination = 255
        pf = (pgn >> 8) & 0xFF
        ps = pgn & 0xFF if pf >= 240 else 255  # PDU2 format for this PGN
        dp = (pgn >> 16) & 0x01
        
        can_id = ((priority & 0x07) << 26) | (dp << 24) | (pf << 16) | (ps << 8) | self.source_address
        
        # Increment sequence counter (0-7)
        seq = self.sequence_counter
        self.sequence_counter = (self.sequence_counter + 1) & 0x07
        
        # First frame: seq|0, length, 6 data bytes
        frame0 = bytearray(8)
        frame0[0] = (seq << 5) | 0
        frame0[1] = total_bytes
        frame0[2:8] = data[0:6]
        frames.append((can_id, bytes(frame0)))
        
        # Subsequent frames: seq|frame_num, 7 data bytes
        offset = 6
        frame_num = 1
        while offset < total_bytes:
            frame = bytearray(8)
            frame[0] = (seq << 5) | frame_num
            chunk = data[offset:offset+7]
            frame[1:1+len(chunk)] = chunk
            # Pad with 0xFF if needed
            for i in range(1+len(chunk), 8):
                frame[i] = 0xFF
            frames.append((can_id, bytes(frame)))
            offset += 7
            frame_num += 1
        
        return frames
    
    def send_engine_load(self, efficiency_percent: float, engine_instance: int = 0):
        """
        Send efficiency as Engine Load % via PGN 127489.
        
        Args:
            efficiency_percent: Polar efficiency (0-125%)
            engine_instance: Engine instance number (default 0)
        """
        if not self.serial or not self.serial.is_open:
            return False
        
        try:
            # Build the PGN payload
            payload = self._build_pgn_127489(efficiency_percent, engine_instance)
            
            # Build fast-packet frames
            frames = self._build_fast_packet_frames(self.PGN_ENGINE_DYNAMIC, payload)
            
            # Send each frame in RAW format
            # RAW format: timestamp,prio,pgn,src,dst,len,data_hex
            timestamp = int(time.time() * 1000) % 86400000  # ms since midnight
            
            for can_id, frame_data in frames:
                # Extract components from CAN ID for RAW format
                priority = (can_id >> 26) & 0x07
                pgn = self.PGN_ENGINE_DYNAMIC
                dst = 255  # Broadcast
                
                # Format: TIMESTAMP,PRIO,PGN,SRC,DST,LEN,DATA
                hex_data = ','.join(f'{b:02X}' for b in frame_data)
                raw_msg = f"{timestamp},{priority},{pgn},{self.source_address},{dst},{len(frame_data)},{hex_data}\r\n"
                
                self.serial.write(raw_msg.encode('ascii'))
                
            return True
            
        except Exception as e:
            print(f"Error sending N2K message: {e}")
            return False


class SailingEfficiencyMonitor:
    """
    Main application: monitors Signal K data and broadcasts polar efficiency.
    """
    
    def __init__(self, 
                 signalk_uri: str = "ws://localhost:3000/signalk/v1/stream?subscribe=none",
                 n2k_port: str = '/dev/ttyACM0',
                 averaging_window: float = 10.0,
                 update_interval: float = 1.0):
        """
        Args:
            signalk_uri: Signal K WebSocket URI
            n2k_port: Serial port for YDNU-02 gateway
            averaging_window: Seconds of data to average
            update_interval: Seconds between N2K transmissions
        """
        self.signalk_uri = signalk_uri
        self.averaging_window = averaging_window
        self.update_interval = update_interval
        
        self.polar_model = BoatPerformance()
        self.gateway = NMEA2000Gateway(port=n2k_port)
        self.data_buffer: deque = deque(maxlen=1000)
        self.current_data = SailingData()
        self.running = False
        
    async def _subscribe_signalk(self, websocket):
        """Subscribe to required Signal K paths"""
        subscribe_msg = {
            "context": "vessels.self",
            "subscribe": [
                {"path": "navigation.speedThroughWater", "period": 1000},
                {"path": "environment.wind.angleTrueWater", "period": 1000},
                {"path": "environment.wind.speedTrue", "period": 1000}
            ]
        }
        await websocket.send(json.dumps(subscribe_msg))
        
    def _rad_to_deg(self, radians: Optional[float]) -> float:
        """Convert radians to degrees"""
        if radians is None:
            return 0.0
        return radians * (180 / math.pi)
    
    def _ms_to_knots(self, ms: Optional[float]) -> float:
        """Convert m/s to knots"""
        if ms is None:
            return 0.0
        return ms * 1.94384
    
    def _process_signalk_update(self, data: dict):
        """Process Signal K delta update"""
        if 'updates' not in data:
            return
            
        for update in data['updates']:
            for value in update.get('values', []):
                path = value.get('path')
                val = value.get('value')
                
                if path == "navigation.speedThroughWater":
                    self.current_data.stw = self._ms_to_knots(val)
                elif path == "environment.wind.angleTrueWater":
                    self.current_data.twa = self._rad_to_deg(val)
                elif path == "environment.wind.speedTrue":
                    self.current_data.tws = self._ms_to_knots(val)
                    
        self.current_data.timestamp = time.time()
        self.data_buffer.append(SailingData(
            stw=self.current_data.stw,
            twa=self.current_data.twa,
            tws=self.current_data.tws,
            timestamp=self.current_data.timestamp
        ))
    
    def _calculate_averaged_efficiency(self) -> tuple:
        """
        Calculate efficiency from averaged recent data.
        Returns (efficiency_percent, target_speed, avg_stw, avg_twa, avg_tws)
        """
        now = time.time()
        cutoff = now - self.averaging_window
        
        # Filter recent data
        recent = [d for d in self.data_buffer if d.timestamp >= cutoff]
        
        if not recent:
            return 0.0, 0.0, 0.0, 0.0, 0.0
        
        # Calculate averages
        avg_stw = sum(d.stw for d in recent) / len(recent)
        avg_twa = sum(d.twa for d in recent) / len(recent)
        avg_tws = sum(d.tws for d in recent) / len(recent)
        
        # Calculate efficiency
        try:
            efficiency, target = self.polar_model.calculate_efficiency(avg_tws, avg_twa, avg_stw)
            return efficiency, target, avg_stw, avg_twa, avg_tws
        except Exception as e:
            print(f"Efficiency calculation error: {e}")
            return 0.0, 0.0, avg_stw, avg_twa, avg_tws
    
    async def _signalk_listener(self):
        """Coroutine to listen to Signal K updates"""
        while self.running:
            try:
                async with websockets.connect(self.signalk_uri) as websocket:
                    print("Connected to Signal K")
                    await self._subscribe_signalk(websocket)
                    
                    while self.running:
                        try:
                            message = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                            data = json.loads(message)
                            self._process_signalk_update(data)
                        except asyncio.TimeoutError:
                            continue
                        except Exception as e:
                            print(f"Signal K receive error: {e}")
                            break
                            
            except Exception as e:
                print(f"Signal K connection error: {e}")
                if self.running:
                    await asyncio.sleep(5)  # Reconnect delay
    
    async def _n2k_transmitter(self):
        """Coroutine to periodically transmit efficiency to N2K"""
        while self.running:
            try:
                efficiency, target, stw, twa, tws = self._calculate_averaged_efficiency()
                
                # Send to N2K network
                self.gateway.send_engine_load(efficiency)
                
                # Console output
                print(f"TWS: {tws:5.1f}kt | TWA: {twa:5.1f}° | STW: {stw:5.1f}kt | "
                      f"Target: {target:5.2f}kt | Efficiency: {efficiency:5.1f}%")
                
            except Exception as e:
                print(f"N2K transmit error: {e}")
            
            await asyncio.sleep(self.update_interval)
    
    async def run(self):
        """Start the monitoring system"""
        print("Initializing Sailing Efficiency Monitor...")
        print(f"  Polar model: Sister Ship data")
        print(f"  Averaging window: {self.averaging_window}s")
        print(f"  Update interval: {self.update_interval}s")
        
        # Connect to N2K gateway
        if not self.gateway.connect():
            print("WARNING: N2K gateway not available - running in monitor-only mode")
        
        self.running = True
        
        try:
            # Run both tasks concurrently
            await asyncio.gather(
                self._signalk_listener(),
                self._n2k_transmitter()
            )
        except KeyboardInterrupt:
            print("\nShutting down...")
        finally:
            self.running = False
            self.gateway.disconnect()


def find_ydnu_port() -> str:
    """Attempt to find the YDNU-02 serial port"""
    import glob
    
    # Common locations
    candidates = [
        '/dev/ttyACM0',
        '/dev/ttyACM1', 
        '/dev/ttyUSB0',
        '/dev/ttyUSB1'
    ]
    
    # Check for Yacht Devices by USB ID
    usb_pattern = '/dev/serial/by-id/*0483_A217*'
    yd_matches = glob.glob(usb_pattern)
    if yd_matches:
        return yd_matches[0]
    
    # Fall back to checking if ports exist
    for port in candidates:
        try:
            s = serial.Serial(port, timeout=0.1)
            s.close()
            return port
        except:
            continue
    
    return '/dev/ttyACM0'  # Default


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Sailing Efficiency Monitor')
    parser.add_argument('--signalk', default='ws://localhost:3000/signalk/v1/stream?subscribe=none',
                        help='Signal K WebSocket URI')
    parser.add_argument('--port', default=None, help='Serial port for YDNU-02')
    parser.add_argument('--window', type=float, default=10.0,
                        help='Averaging window in seconds')
    parser.add_argument('--interval', type=float, default=5.0,
                        help='N2K update interval in seconds')
    
    args = parser.parse_args()
    
    n2k_port = args.port or find_ydnu_port()
    print(f"Using N2K gateway port: {n2k_port}")
    
    monitor = SailingEfficiencyMonitor(
        signalk_uri=args.signalk,
        n2k_port=n2k_port,
        averaging_window=args.window,
        update_interval=args.interval
    )
    
    asyncio.run(monitor.run())
