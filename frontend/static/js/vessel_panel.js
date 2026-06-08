const VesselPanel = {
    selectedShipMmsi: null,
    onShipClick: null,
    shipMarkers: {},

    init(onShipClick) {
        this.onShipClick = onShipClick;
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
                WindfarmMap.shipLayer.removeLayer(this.shipMarkers[mmsi]);
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
                marker.addTo(WindfarmMap.shipLayer);
                this.shipMarkers[mmsiStr] = marker;
            }

            if (mmsiStr === this.selectedShipMmsi) {
                if (!this.shipMarkers[mmsiStr]._highlightRing) {
                    this.shipMarkers[mmsiStr]._highlightRing = L.circleMarker([lat, lng], {
                        radius: size,
                        color: '#4fc3f7',
                        weight: 2,
                        fill: false
                    }).addTo(WindfarmMap.shipLayer);
                }
                this.shipMarkers[mmsiStr]._highlightRing.setLatLng([lat, lng]);
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
                WindfarmMap.shipLayer.removeLayer(m._highlightRing);
                m._highlightRing = null;
            }
        }
        this.selectedShipMmsi = null;
    },

    showShipInfo(ship, risk) {
        const panel = document.getElementById('shipInfoPanel');
        panel.classList.add('visible');

        document.getElementById('infoMmsi').textContent = ship.mmsi || '-';
        document.getElementById('infoSpeed').textContent = ship.speed != null ? (ship.speed + ' kn') : '-';
        document.getElementById('infoCourse').textContent = ship.course != null ? (ship.course + '°') : '-';
        document.getElementById('infoDraught').textContent = ship.draught != null ? (ship.draught + ' m') : '-';
        document.getElementById('infoShipType').textContent = this.formatShipType(ship.ship_type);
        document.getElementById('infoNavStatus').textContent = this.formatNavStatus(ship.nav_status);

        if (risk) {
            const levelEl = document.getElementById('infoRiskLevel');
            levelEl.textContent = this.formatRiskLevel(risk.risk_level);
            levelEl.className = 'info-value risk-' + (risk.risk_level || 'safe');

            const score = risk.risk_score;
            document.getElementById('infoRiskScore').textContent = score != null ? (score > 1 ? score.toFixed(0) : (score * 100).toFixed(1) + '%') : '-';
            document.getElementById('infoDcpa').textContent = risk.dcpa != null ? (risk.dcpa.toFixed(0) + ' m') : '-';
            document.getElementById('infoTcpa').textContent = risk.tcpa != null ? (risk.tcpa.toFixed(1) + ' min') : '-';
            document.getElementById('infoEta').textContent = risk.estimated_entry_time != null ? this.formatEta(risk.estimated_entry_time) : '-';
        } else {
            const levelEl = document.getElementById('infoRiskLevel');
            levelEl.textContent = '安全';
            levelEl.className = 'info-value risk-safe';
            document.getElementById('infoRiskScore').textContent = '-';
            document.getElementById('infoDcpa').textContent = '-';
            document.getElementById('infoTcpa').textContent = '-';
            document.getElementById('infoEta').textContent = '-';
        }
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
    },

    formatShipType(type) {
        const map = {
            cargo: '货船',
            tanker: '油轮',
            passenger: '客船',
            fishing: '渔船',
            tug: '拖船',
            other: '其他'
        };
        return map[type] || type || '-';
    },

    formatNavStatus(status) {
        if (status == null) return '-';
        const map = {
            under_way: '航行中',
            at_anchor: '锚泊',
            not_under_command: '不受指挥',
            restricted_manoeuvrability: '机动受限',
            0: '航行中',
            1: '锚泊',
            2: '不受指挥',
            3: '机动受限',
        };
        return map[status] || ('状态 ' + status);
    },

    formatRiskLevel(level) {
        const map = {
            safe: '安全',
            caution: '注意',
            warning: '警告',
            danger: '危险',
            low: '安全',
            medium: '注意',
            high: '危险'
        };
        return map[level] || level || '-';
    },

    formatEta(minutes) {
        if (minutes == null) return '-';
        if (minutes < 0) return '已进入';
        if (minutes < 1) return '即将进入';
        if (minutes < 60) return Math.round(minutes) + ' 分钟';
        return (minutes / 60).toFixed(1) + ' 小时';
    }
};
