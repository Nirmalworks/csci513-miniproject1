"""This file is the main controller file.

Here, you will design the controller for your for the adaptive cruise control system.
"""

import math

from mp1_simulator.simulator import Observation, Mode

from typing import Tuple


# NOTE: Very important that the class name remains the same
class Controller:
    def __init__(self, distance_threshold: float):
        self.distance_threshold = distance_threshold

        # NEW: memory between calls
        self.previous_distance = None   # distance on the last call (None = no lead car)
        self.lead_speed = 0.0           # our running guess of the lead car's speed (m/s)

    def run_step(self, obs: Observation) -> Tuple[float, Mode]:
        """This is the main run step of the controller.

        Here, you will have to read in the observations `obs`, process it, and output an
        acceleration value and the operation mode.
        The acceleration value must be some value between -10.0 and 10.0.

        For the operation mode output True when "following" and False when "cruising".

        Note that the acceleration value is really some control input that is used
        internally to compute the throttle an brake values of the car.

        Below is some example code where the car just outputs the control value 10.0
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
