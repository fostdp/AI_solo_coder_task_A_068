const MapModule = {
    map: null,
    canvasOverlay: null,
    shipLayer: null,
    turbineLayer: null,
    cableLayer: null,
    zoneLayer: null,
    collisionLayer: null,
    selectedShipMmsi: null,
    onShipClick: null,
    shipMarkers: {},

    init(onShipClick) {
        this.onShipClick = onShipClick;

        this.map = L.map('map', {
            center: [31.0, 121.5],
            zoom: 12,
            zoomControl: true,
            attributionControl: false
        });

        L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
            maxZoom: 19,
            subdomains: 'abcd'
        }).addTo(this.map);

        this.turbineLayer = L.layerGroup().addTo(this.map);
        this.cableLayer = L.layerGroup().addTo(this.map);
        this.zoneLayer = L.layerGroup().addTo(this.map);
        this.collisionLayer = L.layerGroup().addTo(this.map);
        this.shipLayer = L.layerGroup().addTo(this.map);

        this.map.on('click', () => {
            this.selectedShipMmsi = null;
        });
    },

    drawTurbines(turbines) {
        this.turbineLayer.clearLayers();
        if (!turbines) return;

        for (const t of turbines) {
            const label = t.turbine_id || t.name || '';
            const icon = L.divIcon({
                html: '<div style="color:#fff;font-size:14px;text-align:center;line-height:1;">▲<div style="font-size:8px;margin-top:1px;">' + label + '</div></div>',
                className: '',
                iconSize: [30, 24],
                iconAnchor: [15, 12]
            });
            L.marker([t.lat, t.lng], { icon: icon })
                .addTo(this.turbineLayer);
        }
    },

    drawCables(cables) {
        this.cableLayer.clearLayers();
        if (!cables) return;

        for (const c of cables) {
            const routePoints = c.points || c.route || [];
            if (routePoints.length < 2) continue;
            const latlngs = routePoints.map(p => [p[1], p[0]]);
            L.polyline(latlngs, {
                color: '#ff9800',
                weight: 2,
                opacity: 0.7,
                dashArray: '8,6'
            }).addTo(this.cableLayer);
        }
    },

    drawRestrictedZones(zones) {
        this.zoneLayer.clearLayers();
        if (!zones) return;

        for (const z of zones) {
            let lat, lng;
            if (z.center) {
                lng = z.center[0];
                lat = z.center[1];
            } else {
                lat = z.center_lat || z.lat;
                lng = z.center_lng || z.lng;
            }
            L.circle([lat, lng], {
                radius: z.radius_meters || z.radius || 500,
                color: '#f44336',
                fillColor: '#f44336',
                fillOpacity: 0.12,
                weight: 1.5,
                opacity: 0.5,
                dashArray: '5,5'
            }).addTo(this.zoneLayer);
        }
    },

    drawShips(ships, riskAssessments) {
        if (!ships) return;
        const riskMap = {};
        if (riskAssessments) {
            for (const r of riskAssessments) {
                riskMap[r.mmsi] = r;
            }
        }

        const currentMmsis = new Set(ships.map(s => String(s.mmsi)));

        for (const mmsi of Object.keys(this.shipMarkers)) {
            if (!currentMmsis.has(mmsi)) {
                this.shipLayer.removeLayer(this.shipMarkers[mmsi]);
                delete this.shipMarkers[mmsi];
            }
        }

        for (const ship of ships) {
            const risk = riskMap[ship.mmsi];
            const color = this.getShipColor(risk ? risk.risk_level : 'safe');
            const size = this.getShipSize(ship.ship_type);
            const rotation = ship.course || 0;
            const lat = ship.lat;
            const lng = ship.lng;

            const html = '<div style="position:relative;width:' + size + 'px;height:' + size + 'px;">' +
                '<div style="width:0;height:0;border-left:' + (size / 2) + 'px solid transparent;border-right:' + (size / 2) + 'px solid transparent;border-bottom:' + size + 'px solid ' + color + ';transform:rotate(' + rotation + 'deg);transform-origin:center bottom;"></div>' +
                '</div>';

            const icon = L.divIcon({
                html: html,
                className: '',
                iconSize: [size, size],
                iconAnchor: [size / 2, size / 2]
            });

            const mmsiStr = String(ship.mmsi);

            if (this.shipMarkers[mmsiStr]) {
                this.shipMarkers[mmsiStr].setLatLng([lat, lng]);
                this.shipMarkers[mmsiStr].setIcon(icon);
            } else {
                const marker = L.marker([lat, lng], { icon: icon });
                marker.on('click', (e) => {
                    L.DomEvent.stopPropagation(e);
                    this.selectedShipMmsi = mmsiStr;
                    if (this.onShipClick) this.onShipClick(ship);
                });
                marker.addTo(this.shipLayer);
                this.shipMarkers[mmsiStr] = marker;
            }

            if (mmsiStr === this.selectedShipMmsi) {
                if (!this.shipMarkers[mmsiStr]._highlightRing) {
                    this.shipMarkers[mmsiStr]._highlightRing = L.circleMarker([lat, lng], {
                        radius: size,
                        color: '#4fc3f7',
                        weight: 2,
                        fill: false
                    }).addTo(this.shipLayer);
                }
                this.shipMarkers[mmsiStr]._highlightRing.setLatLng([lat, lng]);
            }
        }
    },

    drawCollisionVectors(riskAssessments) {
        this.collisionLayer.clearLayers();
        if (!riskAssessments) return;

        for (const r of riskAssessments) {
            const dcpa = r.dcpa;
            const tcpa = r.tcpa;
            const isCollisionRisk = (dcpa != null && dcpa < 500 && tcpa != null && tcpa < 10)
                || r.risk_score > 0.7;

            if (isCollisionRisk && r.ship_lat && r.ship_lng && r.target_lat && r.target_lng) {
                L.polyline(
                    [[r.ship_lat, r.ship_lng], [r.target_lat, r.target_lng]],
                    { color: '#f44336', weight: 2, opacity: 0.8, dashArray: '4,4' }
                ).addTo(this.collisionLayer);
            }
        }
    },

    updateShipPositions(ships) {
        if (!ships) return;
        for (const ship of ships) {
            const mmsiStr = String(ship.mmsi);
            if (this.shipMarkers[mmsiStr]) {
                this.shipMarkers[mmsiStr].setLatLng([ship.lat, ship.lng]);
            }
        }
    },

    clearSelection() {
        if (this.selectedShipMmsi && this.shipMarkers[this.selectedShipMmsi]) {
            const m = this.shipMarkers[this.selectedShipMmsi];
            if (m._highlightRing) {
                this.shipLayer.removeLayer(m._highlightRing);
                m._highlightRing = null;
            }
        }
        this.selectedShipMmsi = null;
    },

    getShipColor(riskLevel) {
        const colors = {
            safe: '#4caf50',
            caution: '#fdd835',
            warning: '#ff9800',
            danger: '#f44336',
            low: '#4caf50',
            medium: '#ff9800',
            high: '#f44336'
        };
        return colors[riskLevel] || colors.safe;
    },

    getShipSize(shipType) {
        const sizes = {
            cargo: 14,
            tanker: 16,
            passenger: 12,
            fishing: 8,
            tug: 10,
            other: 10
        };
        return sizes[shipType] || sizes.other;
    }
};
