import numpy as np
import pandas as pd
from scipy.interpolate import LinearNDInterpolator
import math

class BoatPerformance:
    def __init__(self):
        self.interpolator = self._build_model()

    def _build_model(self):
        """
        Converts the static polar table (Sister Ship) into a 
        LinearNDInterpolator model.
        """
        # 1. Define the columns (True Wind Speeds) from your image
        tws_cols = [4, 6, 8, 10, 12, 14, 16, 20, 24]

        # 2. Define the data exactly as seen in the image
        # Format: 'Label': [Values for each TWS]
        raw_data = {
            # The dynamic Upwind row (Angle)
            'beat_angle': [42.1, 42.1, 40.4, 39.8, 39.6, 39.2, 39.1, 39.7, 41.1],
            # The dynamic Upwind row (VMG) -> We must convert this to Speed
            'beat_vmg':   [2.59, 3.48, 4.10, 4.46, 4.64, 4.72, 4.75, 4.71, 4.58],
            
            # Fixed Angle Rows (The value is Boat Speed)
            52:  [3.95, 5.21, 6.02, 6.44, 6.64, 6.73, 6.77, 6.77, 6.71],
            60:  [4.15, 5.44, 6.23, 6.62, 6.82, 6.92, 6.96, 6.99, 6.97],
            75:  [4.30, 5.60, 6.37, 6.76, 6.99, 7.14, 7.23, 7.33, 7.38],
            90:  [4.20, 5.51, 6.33, 6.80, 7.04, 7.24, 7.40, 7.64, 7.76],
            110: [4.13, 5.56, 6.53, 7.01, 7.32, 7.56, 7.71, 7.96, 8.11],
            120: [4.00, 5.43, 6.44, 6.97, 7.31, 7.62, 7.90, 8.27, 8.55],
            135: [3.52, 4.90, 6.00, 6.71, 7.12, 7.45, 7.77, 8.43, 9.18],
            150: [2.94, 4.15, 5.21, 6.10, 6.70, 7.06, 7.34, 7.90, 8.52],
            
            # The dynamic Downwind row (VMG) -> Convert to Speed
            'run_vmg':    [2.54, 3.60, 4.51, 5.28, 5.83, 6.25, 6.59, 7.14, 7.63],
            # The dynamic Downwind row (Angle)
            'gybe_angle': [144.2, 144.2, 148.0, 149.3, 153.1, 159.0, 163.0, 176.2, 175.9]
        }

        points = [] # (tws, twa)
        values = [] # target_speed

        for i, tws in enumerate(tws_cols):
            # --- A. Process Beat (Upwind) ---
            b_ang = raw_data['beat_angle'][i]
            b_vmg = raw_data['beat_vmg'][i]
            # Calculate Speed from VMG: Speed = VMG / cos(radians(angle))
            b_spd = b_vmg / math.cos(math.radians(b_ang))
            points.append((tws, b_ang))
            values.append(b_spd)
            # Add 0-angle boundary (Head to wind = 0 speed)
            points.append((tws, 0))
            values.append(0.0)

            # --- B. Process Fixed Angles ---
            for angle in [52, 60, 75, 90, 110, 120, 135, 150]:
                spd = raw_data[angle][i]
                points.append((tws, angle))
                values.append(spd)

            # --- C. Process Run (Downwind) ---
            r_ang = raw_data['gybe_angle'][i]
            r_vmg = raw_data['run_vmg'][i]
            # Speed = VMG / cos(radians(180 - angle)) ? 
            # Actually for VMG downwind, the formula is VMG = Spd * -cos(angle)
            # So Spd = VMG / abs(cos(angle))
            r_spd = r_vmg / abs(math.cos(math.radians(r_ang)))
            points.append((tws, r_ang))
            values.append(r_spd)
            # Add 180-angle boundary (Dead downwind). 
            # We approximate dead run speed slightly less than gybe speed if not provided,
            # but linear interpolation between gybe angles works fine here.
            points.append((tws, 180))
            values.append(r_spd * 0.95) # Slight penalty for dead downwind if not specified

        # Create the triangulation model
        # Fill_value=0 means if we are outside the wind range (e.g. 30kts), return 0 
        # (or we could handle extrapolation later)
        return LinearNDInterpolator(points, values, fill_value=0)

    def get_target_speed(self, tws, twa):
        """
        Returns theoretical hull speed.
        Handles TWA > 180 (port/starboard symmetry).
        """
        # Sanitize inputs
        if pd.isna(tws) or pd.isna(twa):
            return None
        
        # Handle symmetry (0-180)
        twa = abs(twa)
        if twa > 180:
            twa = 360 - twa
            
        # Query the model
        # The interpolator expects an array of points
        res = self.interpolator([tws, twa])[0]
        
        # Float conversion (numpy float to python float)
        return float(res)

    def calculate_efficiency(self, tws, twa, stw):
        target = self.get_target_speed(tws, twa)
        
        if target <= 0.5: # Avoid division by zero in light air/calm
            return 0.0
            
        efficiency = (stw / target) * 100.0
        
        # Cap efficiency for the "Engine Load" display (usually 0-100 or 0-125)
        # We cap at 125% in case you are surfing down a wave
        return min(max(efficiency, 0.0), 125.0), target
