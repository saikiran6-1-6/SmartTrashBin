# PROG6002 Assignment 1 - Smart 3-Compartment Trash Bin
# Running on Pico W in Wokwi (stand-in for Raspberry Pi Zero)


from machine import Pin, PWM, time_pulse_us
import time
import json

# Pin mapping - matched to our Wokwi diagram
SENSORS = {
    "recycled": {"trig": 5,  "echo": 6},
    "green":    {"trig": 13, "echo": 19},
    "landfill": {"trig": 26, "echo": 21}
}

SERVO_PIN = 12
BIN_HEIGHT_CM = 30.0      # assumed height of each compartment
WINDOW_SIZE = 5           # how many readings we keep for the moving average

# -------------------------------------------------
# Ultrasonic sensor class
# Each sensor keeps its own list of recent readings
# so we can smooth out the noisy HC-SR04 values
# -------------------------------------------------
class Ultrasonic:
    def __init__(self, trig_pin, echo_pin):
        self.trig = Pin(trig_pin, Pin.OUT)
        self.echo = Pin(echo_pin, Pin.IN)
        self.readings = []   # stores last few distance values

    def distance_cm(self):
        # standard HC-SR04 trigger sequence
        self.trig.low()
        time.sleep_us(2)
        self.trig.high()
        time.sleep_us(10)
        self.trig.low()

        try:
            # wait for the echo pulse (timeout 30 ms)
            pulse = time_pulse_us(self.echo, 1, 30000)
            if pulse < 0:
                return None
            # convert pulse time to distance in cm
            return (pulse * 0.0343) / 2
        except:
            return None

    def smoothed_distance(self):
        d = self.distance_cm()
        if d is not None and 2 < d < 400:          # ignore crazy values
            self.readings.append(d)
            # only keep the last WINDOW_SIZE readings
            if len(self.readings) > WINDOW_SIZE:
                self.readings.pop(0)

        if len(self.readings) == 0:
            return BIN_HEIGHT_CM                  # default if no valid reading yet
        return sum(self.readings) / len(self.readings)

# create one sensor object for each compartment
sensors = {name: Ultrasonic(cfg["trig"], cfg["echo"]) for name, cfg in SENSORS.items()}

# -------------------------------------------------
# Servo setup
# Using hardware PWM on GP12
# -------------------------------------------------
servo = PWM(Pin(SERVO_PIN))
servo.freq(50)

def set_servo_angle(angle):
    # maps 0-180 degrees to the duty cycle the SG90 expects
    duty = int(1000 + (angle / 180) * 8000)
    servo.duty_u16(duty)

# positions the flap should move to for each compartment
SERVO_POSITIONS = {
    "recycled": 30,
    "green": 90,
    "landfill": 150,
    "idle": 90
}

# -------------------------------------------------
# Simple offline queue for when MQTT is down
# (in real Pi Zero we would use paho-mqtt)
# -------------------------------------------------
offline_queue = []
mqtt_connected = True          # we pretend it is connected in the simulation

def publish_telemetry(payload):
    msg = json.dumps(payload)
    if mqtt_connected:
        print("MQTT PUBLISH ->", msg)
    else:
        offline_queue.append(msg)
        print("OFFLINE - queued. Depth:", len(offline_queue))

def get_fill_percent(distance_cm):
    # convert distance from the top of the bin into a fill percentage
    if distance_cm is None:
        return None
    fill = ((BIN_HEIGHT_CM - distance_cm) / BIN_HEIGHT_CM) * 100
    return max(0.0, min(100.0, round(fill, 1)))

# -------------------------------------------------
# Main loop
# -------------------------------------------------
print("Smart Bin started - waiting for first publish...")
set_servo_angle(90)            # start in the middle

loop_count = 0
PUBLISH_EVERY = 20             # publish roughly every 4-5 seconds

while True:
    loop_count += 1

    # Read all three sensors
    capacities = {}
    for name, sensor in sensors.items():
        dist = sensor.smoothed_distance()
        fill = get_fill_percent(dist)
        status = "ok" if dist is not None else "sensor_fault"

        capacities[name] = {
            "fill_percent": fill,
            "raw_distance_cm": round(dist, 1) if dist is not None else None,
            "status": status
        }

    # Move servo to the fullest compartment 
    valid = {}
    for n, d in capacities.items():
        if d["fill_percent"] is not None:
            valid[n] = d["fill_percent"]

    if valid:
        highest = max(valid, key=valid.get)
        angle = SERVO_POSITIONS.get(highest, 90)
        set_servo_angle(angle)
        print("Servo ->", highest, "angle:", angle)
    else:
        set_servo_angle(90)

    # Build and "publish" telemetry
    if loop_count % PUBLISH_EVERY == 0:
        payload = {
            "schema_version": "1.0",
            "bin_id": "BIN-001",
            "timestamp": loop_count,
            "firmware_version": "1.0.0-wokwi",
            "capacities": capacities,
            "system_health": {
                "mqtt_connected": mqtt_connected,
                "offline_queue_depth": len(offline_queue)
            },
            "alerts": []
        }

        # check if any bin is getting full
        for name, data in capacities.items():
            if data["fill_percent"] is not None and data["fill_percent"] > 85:
                payload["alerts"].append(name + "_above_threshold")

        publish_telemetry(payload)

    time.sleep(0.2)