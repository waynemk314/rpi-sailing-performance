import pandas as pd
import numpy as np
import time
from polars import BoatPerformance

def run_simulation():
    print("Initializing Polar Model from Sister Ship data...")
    perf = BoatPerformance()
    
    print("Starting Simulation (Press Ctrl+C to stop)...")
    print(f"{'TWS (kt)':<10} {'TWA (deg)':<10} {'STW (kt)':<10} || {'Target':<10} {'Efficiency':<10}")
    print("-" * 60)

    # Create a fake rolling buffer
    # We will simulate 10 seconds of data arriving
    buffer = []

    try:
        while True:
            # 1. Generate Mock Sensor Data with some noise
            # Let's pretend we are sailing upwind in 12 knots of wind
            mock_tws = np.random.normal(12, 1.5)  # Mean 12, SD 1.5
            mock_twa = np.random.normal(45, 2.0)  # Mean 45 deg
            
            # Let's pretend we are sailing at 6.8 knots (Sister ship target is ~6.64 at 52 deg)
            mock_stw = np.random.normal(6.8, 0.2)

            # 2. Add to rolling buffer (List of dictionaries)
            buffer.append({
                'timestamp': pd.Timestamp.now(),
                'tws': mock_tws,
                'twa': mock_twa,
                'stw': mock_stw
            })

            # 3. Keep only last 10 seconds
            now = pd.Timestamp.now()
            buffer = [b for b in buffer if (now - b['timestamp']).total_seconds() <= 10]
            
            # 4. Every 5 loops (approx 1 sec in this fake loop), calculate stats
            # Convert to DataFrame for easy averaging
            df = pd.DataFrame(buffer)
            
            if not df.empty:
                avg_tws = df['tws'].mean()
                avg_twa = df['twa'].mean()
                avg_stw = df['stw'].mean()

                # Calculate Performance
                eff_percent, target_spd = perf.calculate_efficiency(avg_tws, avg_twa, avg_stw)

                print(f"{avg_tws:<10.1f} {avg_twa:<10.1f} {avg_stw:<10.1f} || {target_spd:<10.2f} {eff_percent:<10.1f}%")

            time.sleep(0.5) # Update twice a second

    except KeyboardInterrupt:
        print("\nSimulation stopped.")

if __name__ == "__main__":
    run_simulation()