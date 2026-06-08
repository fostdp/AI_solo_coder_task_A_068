const HeatmapModule = {
    heatLayer: null,
    isVisible: false,
    refreshTimer: null,
    API_BASE: 'http://localhost:8000/api',

    loadHeatmapData() {
        return fetch(this.API_BASE + '/traffic/heatmap')
            .then(res => {
                if (!res.ok) return null;
                return res.json();
            })
            .catch(e => {
                console.error('Heatmap load error:', e);
                return null;
            });
    },

    renderHeatmap(data) {
        if (!data) return;

        if (this.heatLayer) {
            MapModule.map.removeLayer(this.heatLayer);
        }

        const positions = data.positions || data;
        if (!positions || !positions.length) return;

        const points = positions.map(d => {
            const lat = d.lat;
            const lng = d.lng;
            const intensity = d.intensity || d.count || 0.5;
            return [lat, lng, intensity];
        });

        this.heatLayer = L.heatLayer(points, {
            radius: 20,
            blur: 25,
            maxZoom: 15,
            max: 1.0,
            gradient: {
                0.0: '#0000ff',
                0.25: '#00ff00',
                0.5: '#ffff00',
                0.75: '#ff8800',
                1.0: '#ff0000'
            }
        });

        if (this.isVisible) {
            this.heatLayer.addTo(MapModule.map);
        }
    },

    toggleHeatmap() {
        this.isVisible = !this.isVisible;

        if (this.isVisible) {
            this.loadHeatmapData().then(data => {
                this.renderHeatmap(data);
            });
            this.startAutoRefresh();
        } else {
            this.stopAutoRefresh();
            if (this.heatLayer) {
                MapModule.map.removeLayer(this.heatLayer);
                this.heatLayer = null;
            }
        }
    },

    startAutoRefresh() {
        this.stopAutoRefresh();
        this.refreshTimer = setInterval(() => {
            this.loadHeatmapData().then(data => {
                this.renderHeatmap(data);
            });
        }, 5 * 60 * 1000);
    },

    stopAutoRefresh() {
        if (this.refreshTimer) {
            clearInterval(this.refreshTimer);
            this.refreshTimer = null;
        }
    }
};
