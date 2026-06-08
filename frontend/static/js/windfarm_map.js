const WindfarmMap = {
    map: null,
    turbineLayer: null,
    cableLayer: null,
    zoneLayer: null,
    collisionLayer: null,
    shipLayer: null,

    init() {
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
            VesselPanel.clearSelection();
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
    }
};
