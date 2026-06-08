const App = {
    API_BASE: 'http://localhost:8000/api',
    WS_URL: 'ws://localhost:8000/ws',
    REFRESH_INTERVAL: 5000,

    data: {
        ships: [],
        turbines: [],
        cables: [],
        restrictedZones: [],
        alerts: [],
        riskAssessments: []
    },

    ws: null,
    wsReconnectTimer: null,
    refreshTimer: null,
    selectedShip: null,

    init() {
        MapModule.init(this.handleShipClick.bind(this));
        this.connectWebSocket();
        this.loadInitialData();
        this.startPeriodicRefresh();
        this.bindUIEvents();
        StatsModule.loadMonthlyStats();
    },

    connectWebSocket() {
        this.updateWsStatus('connecting');
        try {
            this.ws = new WebSocket(this.WS_URL);
        } catch (e) {
            this.updateWsStatus('disconnected');
            this.scheduleReconnect();
            return;
        }

        this.ws.onopen = () => {
            this.updateWsStatus('connected');
        };

        this.ws.onmessage = (event) => {
            try {
                const msg = JSON.parse(event.data);
                this.handleWsMessage(msg);
            } catch (e) {
                console.error('WS parse error:', e);
            }
        };

        this.ws.onclose = () => {
            this.updateWsStatus('disconnected');
            this.scheduleReconnect();
        };

        this.ws.onerror = () => {
            this.updateWsStatus('disconnected');
        };
    },

    scheduleReconnect() {
        if (this.wsReconnectTimer) return;
        this.wsReconnectTimer = setTimeout(() => {
            this.wsReconnectTimer = null;
            this.connectWebSocket();
        }, 5000);
    },

    updateWsStatus(status) {
        const dot = document.getElementById('wsStatusDot');
        const text = document.getElementById('wsStatusText');
        dot.className = 'status-dot ' + status;
        const labels = { connecting: '连接中...', connected: '已连接', disconnected: '已断开' };
        text.textContent = labels[status] || status;
    },

    handleWsMessage(msg) {
        if (msg.type === 'ship_update') {
            if (msg.ships) this.data.ships = msg.ships;
            if (msg.risk_assessments) this.data.riskAssessments = msg.risk_assessments;
            this.renderShips();
        } else if (msg.type === 'ships') {
            this.data.ships = msg.data || msg.ships || [];
            this.renderShips();
        } else if (msg.type === 'alerts') {
            this.data.alerts = msg.data || msg.alerts || [];
            AlertsModule.updateAlertList(this.data.alerts);
        } else if (msg.type === 'risk') {
            this.data.riskAssessments = msg.data || msg.risk_assessments || msg.risks || [];
            this.renderShips();
        } else if (msg.type === 'alert') {
            this.data.alerts.unshift(msg.data || msg);
            AlertsModule.updateAlertList(this.data.alerts);
            AlertsModule.playAlertSound((msg.data || msg).level);
        }
    },

    async loadInitialData() {
        try {
            const [ships, turbines, cables, zones, alerts, risk] = await Promise.all([
                this.fetch('/ships'),
                this.fetch('/turbines'),
                this.fetch('/cables'),
                this.fetch('/restricted-zones'),
                this.fetch('/alerts?limit=50'),
                this.fetch('/risk-assessments')
            ]);

            this.data.ships = ships || [];
            this.data.turbines = turbines || [];
            this.data.cables = cables || [];
            this.data.restrictedZones = zones || [];
            this.data.alerts = alerts || [];
            this.data.riskAssessments = risk || [];

            MapModule.drawTurbines(this.data.turbines);
            MapModule.drawCables(this.data.cables);
            MapModule.drawRestrictedZones(this.data.restrictedZones);
            this.renderShips();
            AlertsModule.updateAlertList(this.data.alerts);
        } catch (e) {
            console.error('Initial data load error:', e);
        }
    },

    async fetch(path) {
        try {
            const res = await fetch(this.API_BASE + path);
            if (!res.ok) return null;
            return await res.json();
        } catch (e) {
            console.error('Fetch error:', path, e);
            return null;
        }
    },

    startPeriodicRefresh() {
        this.refreshTimer = setInterval(() => this.refreshData(), this.REFRESH_INTERVAL);
    },

    async refreshData() {
        const [ships, risk, alerts] = await Promise.all([
            this.fetch('/ships'),
            this.fetch('/risk-assessments'),
            this.fetch('/alerts?limit=50')
        ]);

        if (ships) this.data.ships = ships;
        if (risk) this.data.riskAssessments = risk;
        if (alerts) this.data.alerts = alerts;

        this.renderShips();
        AlertsModule.updateAlertList(this.data.alerts);
        this.updateRiskSummary();
    },

    renderShips() {
        MapModule.drawShips(this.data.ships, this.data.riskAssessments);
        MapModule.drawCollisionVectors(this.data.riskAssessments);
        MapModule.updateShipPositions(this.data.ships);
        this.updateRiskSummary();
        document.getElementById('shipCount').textContent = this.data.ships.length;

        if (this.selectedShip) {
            const updated = this.data.ships.find(s => s.mmsi === this.selectedShip.mmsi);
            const risk = this.data.riskAssessments.find(r => r.mmsi === this.selectedShip.mmsi);
            if (updated) {
                AlertsModule.showShipInfo(updated, risk);
            }
        }
    },

    updateRiskSummary() {
        let high = 0, medium = 0, low = 0;
        for (const r of this.data.riskAssessments) {
            if (r.risk_level === 'danger') high++;
            else if (r.risk_level === 'warning') high++;
            else if (r.risk_level === 'caution') medium++;
            else low++;
        }
        document.getElementById('riskHigh').textContent = high;
        document.getElementById('riskMedium').textContent = medium;
        document.getElementById('riskLow').textContent = low;
    },

    updateSingleShip(shipData) {
        const idx = this.data.ships.findIndex(s => s.mmsi === shipData.mmsi);
        if (idx >= 0) {
            this.data.ships[idx] = shipData;
        } else {
            this.data.ships.push(shipData);
        }
        this.renderShips();
    },

    handleShipClick(ship) {
        this.selectedShip = ship;
        const risk = this.data.riskAssessments.find(r => r.mmsi === ship.mmsi);
        AlertsModule.showShipInfo(ship, risk);
    },

    bindUIEvents() {
        document.getElementById('btnCloseShipInfo').addEventListener('click', () => {
            document.getElementById('shipInfoPanel').classList.remove('visible');
            this.selectedShip = null;
            MapModule.clearSelection();
        });

        document.getElementById('btnHeatmap').addEventListener('click', () => {
            HeatmapModule.toggleHeatmap();
            document.getElementById('btnHeatmap').classList.toggle('active');
        });
    }
};

document.addEventListener('DOMContentLoaded', () => App.init());
