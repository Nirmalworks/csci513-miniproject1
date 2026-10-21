"""Adaptive cruise controller.

Each step it estimates the lead car's speed from the change in gap, computes the fastest
speed from which the ego car can still stop behind the lead, and tracks the lower of that
and the desired speed.
"""

import math

from mp1_simulator.simulator import Observation, Mode

from typing import Tuple


# NOTE: Very important that the class name remains the same
class Controller:
    def __init__(self, distance_threshold: float):
        self.distance_threshold = distance_threshold

        # memory between calls
        self.previous_distance = None   # distance on the last call (None = no lead car)
        self.lead_speed = 0.0           # our running guess of the lead car's speed (m/s)

    def run_step(self, obs: Observation) -> Tuple[float, Mode]:
        """Return a control value in [-10, 10] and the operation mode for one step.

        The control value is mapped to throttle (positive) or brake (negative) by the
        simulator. The mode is FOLLOWING when the lead car is within 40 m in our lane,
        otherwise CRUISING.
        """

        ego_velocity = obs.ego_velocity
        desired_speed = obs.desired_speed
        dist_to_lead = obs.distance_to_lead


        if not math.isfinite(dist_to_lead):
            self.previous_distance = None
            self.lead_speed = 0.0
            target_speed = desired_speed
            mode = Mode.CRUISING
        else:
            if self.previous_distance is not None:
                guess = ego_velocity + (dist_to_lead - self.previous_distance) / 0.1
                if guess < 0.0:
                    guess = 0.0
                elif guess > 40.0:
                    guess = 40.0
                self.lead_speed = 0.5 * guess + 0.5 * self.lead_speed
            self.previous_distance = dist_to_lead
            room = dist_to_lead - 8.0 + self.lead_speed ** 2 / 16.0

           
            if room <= 0.0:
                safe_speed = 0.0
            else:
                safe_speed = 3.5 * (math.sqrt(0.25 + 2.0 * room / 3.5) - 0.5)

            if safe_speed < desired_speed:
                target_speed = safe_speed
            else:
                target_speed = desired_speed

            
            if dist_to_lead > 40.0:
                mode = Mode.CRUISING
            else:
                mode = Mode.FOLLOWING

        speed_error = target_speed - ego_velocity
        if speed_error >= 0.0:
            command = 5.0 * speed_error    
        else:
            command = 8.0 * speed_error     

        if command > 10.0:
            command = 10.0
        elif command < -10.0:
            command = -10.0

        return (command, mode)
