import argparse
import asyncio
import json
import math
import random
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

WIND_FARM_CENTER = (121.5, 31.0)
NUM_TURBINES = 80
FARM_RADIUS = 0.12
SHIP_COUNT_RANGE = (15, 25)
ANCHORED_COUNT_RANGE = (3, 5)
COLLISION_COURSE_COUNT_RANGE = (2, 3)
DETECTION_RADIUS_KM = 5.0
CABLE_ROUTE_WAYPOINTS = [
    (121.48, 30.98),
    (121.49, 30.99),
    (121.50, 31.00),
    (121.51, 31.01),
    (121.52, 31.02),
]

SHIP_TYPES = ["cargo", "tanker", "fishing", "passenger", "tug", "other"]
NAV_STATUSES = ["under_way", "at_anchor", "not_under_command", "restricted_manoeuvrability"]

DRAUGHT_BY_TYPE = {
    "cargo": (6.0, 15.0),
    "tanker": (8.0, 15.0),
    "fishing": (3.0, 6.0),
    "passenger": (5.0, 9.0),
    "tug": (3.0, 6.0),
    "other": (3.5, 10.0),
}

SENSOR_TYPES = ["ais", "radar", "sonar"]


def _generate_turbines():
    turbines = []
    rows = 10
    cols = 8
    spacing_lat = (2 * FARM_RADIUS) / (rows - 1)
    spacing_lng = (2 * FARM_RADIUS) / (cols - 1)
    for i in range(NUM_TURBINES):
        row = i // cols
        col = i % cols
        lat = WIND_FARM_CENTER[1] - FARM_RADIUS + row * spacing_lat
        lng = WIND_FARM_CENTER[0] - FARM_RADIUS + col * spacing_lng
        turbines.append({
            "id": f"WT{i + 1:02d}",
            "lat": lat,
            "lng": lng,
        })
    return turbines


def _random_mmsi(used):
    while True:
        mmsi = random.randint(100000000, 999999999)
        if mmsi not in used:
            used.add(mmsi)
            return mmsi


def _haversine_km(lat1, lng1, lat2, lng2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlng / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _knots_to_deg_per_sec(knots):
    nm_per_deg_lat = 60.0
    return knots / nm_per_deg_lat


class Ship:
    def __init__(self, mmsi, ship_type, lat, lng, speed, course, nav_status):
        self.mmsi = mmsi
        self.ship_type = ship_type
        self.lat = lat
        self.lng = lng
        self.speed = speed
        self.course = course
        self.nav_status = nav_status
        draught_range = DRAUGHT_BY_TYPE.get(ship_type, (3.0, 10.0))
        self.draught = round(random.uniform(*draught_range), 1)
        self.scour_depth = round(random.uniform(0.5, 3.0), 1)
        self._scour_target = self.scour_depth
        self._scour_timer = 0

    def update(self, dt):
        if self.nav_status == "at_anchor":
            self.lat += random.gauss(0, 0.00001)
            self.lng += random.gauss(0, 0.00001)
            self.speed = 0
        else:
            variation = random.gauss(0, 2)
            self.course = (self.course + variation) % 360
            speed_var = random.gauss(0, 0.3)
            self.speed = max(0, min(18, self.speed + speed_var))
            deg_per_sec = _knots_to_deg_per_sec(self.speed)
            self.lat += deg_per_sec * math.cos(math.radians(self.course)) * dt
            self.lng += deg_per_sec * math.sin(math.radians(self.course)) * dt / math.cos(math.radians(self.lat))

        self._scour_timer += dt
        if self._scour_timer >= 30:
            self._scour_timer = 0
            self._scour_target = round(max(0.3, min(4.0, self._scour_target + random.gauss(0, 0.3))), 1)
        self.scour_depth += (self._scour_target - self.scour_depth) * 0.05
        self.scour_depth = round(self.scour_depth, 1)

    def is_out_of_area(self):
        max_dist = 0.35
        return (abs(self.lat - WIND_FARM_CENTER[1]) > max_dist
                or abs(self.lng - WIND_FARM_CENTER[0]) > max_dist)

    def to_dict(self):
        return {
            "mmsi": self.mmsi,
            "lat": round(self.lat, 4),
            "lng": round(self.lng, 4),
            "speed": round(self.speed, 1),
            "course": round(self.course, 1),
            "draught": self.draught,
            "ship_type": self.ship_type,
            "nav_status": self.nav_status,
            "scour_depth": round(self.scour_depth, 1),
        }


class ShipFactory:
    def __init__(self):
        self._used_mmsi = set()

    def create(self, lat=None, lng=None, near_turbine=None, anchored=False, near_cable=False):
        mmsi = _random_mmsi(self._used_mmsi)
        ship_type = random.choice(SHIP_TYPES)

        if near_turbine:
            lat = near_turbine["lat"] + random.uniform(-0.008, 0.008)
            lng = near_turbine["lng"] + random.uniform(-0.008, 0.008)
        elif near_cable:
            wp = random.choice(CABLE_ROUTE_WAYPOINTS)
            lat = wp[1] + random.uniform(-0.01, 0.01)
            lng = wp[0] + random.uniform(-0.01, 0.01)
        elif lat is None or lng is None:
            side = random.randint(0, 3)
            offset = random.uniform(0.25, 0.35)
            if side == 0:
                lat = WIND_FARM_CENTER[1] + offset
                lng = WIND_FARM_CENTER[0] + random.uniform(-0.2, 0.2)
                course = random.uniform(150, 210)
            elif side == 1:
                lat = WIND_FARM_CENTER[1] - offset
                lng = WIND_FARM_CENTER[0] + random.uniform(-0.2, 0.2)
                course = random.uniform(330, 390) % 360
            elif side == 2:
                lat = WIND_FARM_CENTER[1] + random.uniform(-0.2, 0.2)
                lng = WIND_FARM_CENTER[0] - offset
                course = random.uniform(60, 120)
            else:
                lat = WIND_FARM_CENTER[1] + random.uniform(-0.2, 0.2)
                lng = WIND_FARM_CENTER[0] + offset
                course = random.uniform(240, 300)

        if anchored:
            speed = 0
            nav_status = "at_anchor"
            course = random.uniform(0, 360)
        else:
            speed = random.uniform(3, 18)
            nav_status = random.choices(
                NAV_STATUSES,
                weights=[0.7, 0.1, 0.1, 0.1],
            )[0]
            if "course" not in dir():
                course = random.uniform(0, 360)

        return Ship(mmsi, ship_type, lat, lng, speed, course, nav_status)


class AISRadarSimulator:
    def __init__(self, broker_host, broker_port, interval, max_steps):
        self.broker_host = broker_host
        self.broker_port = broker_port
        self.interval = interval
        self.max_steps = max_steps
        self.turbines = _generate_turbines()
        self.ship_factory = ShipFactory()
        self.ships = []
        self.step_count = 0
        self.client = mqtt.Client(client_id="ais_radar_simulator", protocol=mqtt.MQTTv311)
        self._init_ships()

    def _init_ships(self):
        total = random.randint(*SHIP_COUNT_RANGE)
        anchored_count = random.randint(*ANCHORED_COUNT_RANGE)
        collision_count = random.randint(*COLLISION_COURSE_COUNT_RANGE)
        normal_count = total - anchored_count - collision_count

        for _ in range(anchored_count):
            wp = random.choice(CABLE_ROUTE_WAYPOINTS)
            ship = self.ship_factory.create(near_cable=True, anchored=True)
            ship.lat = wp[1] + random.uniform(-0.008, 0.008)
            ship.lng = wp[0] + random.uniform(-0.008, 0.008)
            self.ships.append(ship)

        for _ in range(collision_count):
            turbine = random.choice(self.turbines)
            ship = self.ship_factory.create(near_turbine=turbine)
            dx = turbine["lng"] - ship.lng
            dy = turbine["lat"] - ship.lat
            ship.course = math.degrees(math.atan2(dx, dy)) % 360
            ship.speed = random.uniform(5, 12)
            self.ships.append(ship)

        for _ in range(normal_count):
            ship = self.ship_factory.create()
            self.ships.append(ship)

    def _replace_out_of_area_ships(self):
        new_ships = []
        for ship in self.ships:
            if ship.is_out_of_area():
                self.ship_factory._used_mmsi.discard(ship.mmsi)
                new_ship = self.ship_factory.create()
                new_ships.append(new_ship)
            else:
                new_ships.append(ship)
        self.ships = new_ships

    def _get_detected_ships(self, turbine):
        detected = []
        for ship in self.ships:
            dist = _haversine_km(turbine["lat"], turbine["lng"], ship.lat, ship.lng)
            if dist <= DETECTION_RADIUS_KM:
                detected.append((dist, ship))
        detected.sort(key=lambda x: x[0])
        return [s for _, s in detected]

    async def _publish_turbine_data(self):
        loop = asyncio.get_event_loop()
        tasks = []
        for turbine in self.turbines:
            detected = self._get_detected_ships(turbine)
            if not detected:
                continue
            sensor_type = random.choice(SENSOR_TYPES)
            payload = {
                "turbine_id": turbine["id"],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "sensor_type": sensor_type,
                "ships": [s.to_dict() for s in detected],
            }
            topic = f"windfarm/turbine/{turbine['id']}/ais"
            tasks.append(loop.run_in_executor(
                None, self._mqtt_publish, topic, payload
            ))
        await asyncio.gather(*tasks)

    async def _publish_all_ships(self):
        loop = asyncio.get_event_loop()
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "count": len(self.ships),
            "ships": [s.to_dict() for s in self.ships],
        }
        await loop.run_in_executor(
            None, self._mqtt_publish, "windfarm/ships/all", payload
        )

    def _mqtt_publish(self, topic, payload):
        try:
            self.client.publish(topic, json.dumps(payload), qos=1)
        except Exception as e:
            print(f"  [ERROR] Publish to {topic} failed: {e}")

    def _print_status(self):
        anchored = sum(1 for s in self.ships if s.nav_status == "at_anchor")
        near_turbines = 0
        for ship in self.ships:
            for t in self.turbines:
                if _haversine_km(t["lat"], t["lng"], ship.lat, ship.lng) < 1.0:
                    near_turbines += 1
                    break
        print(
            f"Step {self.step_count}: "
            f"{len(self.ships)} ships | "
            f"{anchored} anchored | "
            f"{near_turbines} near turbines (<1km)"
        )

    async def run(self):
        self.client.on_connect = lambda c, u, f, rc: print(f"Connected to MQTT broker at {self.broker_host}:{self.broker_port}")
        try:
            self.client.connect(self.broker_host, self.broker_port, keepalive=60)
        except Exception as e:
            print(f"Failed to connect to MQTT broker: {e}")
            return
        self.client.loop_start()

        print(f"AIS/Radar Simulator started — {len(self.ships)} ships, {len(self.turbines)} turbines")
        print(f"Publish interval: {self.interval}s | Max steps: {self.max_steps or 'infinite'}")
        print("=" * 60)

        try:
            while self.max_steps == 0 or self.step_count < self.max_steps:
                self.step_count += 1
                dt = self.interval
                for ship in self.ships:
                    ship.update(dt)
                self._replace_out_of_area_ships()
                await self._publish_turbine_data()
                await self._publish_all_ships()
                self._print_status()
                await asyncio.sleep(self.interval)
        except KeyboardInterrupt:
            print("\nSimulation stopped by user.")
        finally:
            self.client.loop_stop()
            self.client.disconnect()
            print("MQTT client disconnected.")


def main():
    parser = argparse.ArgumentParser(description="AIS/Radar/Sonar Simulator for Offshore Wind Farm")
    parser.add_argument("--steps", type=int, default=0, help="Number of simulation steps (0=infinite)")
    parser.add_argument("--broker-host", default="localhost", help="MQTT broker host")
    parser.add_argument("--broker-port", type=int, default=1883, help="MQTT broker port")
    parser.add_argument("--interval", type=int, default=10, help="Publish interval in seconds")
    args = parser.parse_args()

    sim = AISRadarSimulator(args.broker_host, args.broker_port, args.interval, args.steps)
    asyncio.run(sim.run())


if __name__ == "__main__":
    main()
