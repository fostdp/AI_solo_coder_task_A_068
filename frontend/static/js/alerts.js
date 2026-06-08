const AlertsModule = {

    updateAlertList(alerts) {
        const container = document.getElementById('alertList');
        if (!alerts || alerts.length === 0) {
            container.innerHTML = '<div class="alert-empty">暂无预警信息</div>';
            return;
        }

        const sorted = [...alerts].sort((a, b) => {
            return new Date(b.timestamp) - new Date(a.timestamp);
        });

        const MAX_ALERTS = 50;
        const display = sorted.slice(0, MAX_ALERTS);

        container.innerHTML = display.map(a => {
            const isLevel1 = a.level === 'level1_collision' || a.level === 'high' || a.level === 1;
            const levelClass = isLevel1 ? 'level1' : 'level2';
            const levelLabel = isLevel1 ? '碰撞预警' : '海缆预警';
            const time = this.formatTime(a.timestamp);
            const desc = this.buildAlertDescription(a);
            return '<div class="alert-item ' + levelClass + '">' +
                '<div class="alert-top">' +
                '<span class="alert-badge ' + levelClass + '">' + levelLabel + '</span>' +
                '<span class="alert-time">' + time + '</span>' +
                '</div>' +
                '<div class="alert-mmsi">MMSI: ' + (a.mmsi || '-') + '</div>' +
                '<div class="alert-desc">' + desc + '</div>' +
                '</div>';
        }).join('');
    },

    buildAlertDescription(a) {
        const details = a.details || {};
        const isCollision = a.level === 'level1_collision' || a.level === 'high' || a.level === 1;
        if (isCollision) {
            let desc = '碰撞风险';
            if (details.dcpa != null) desc += ' DCPA:' + Math.round(details.dcpa) + 'm';
            if (details.tcpa != null) desc += ' TCPA:' + details.tcpa.toFixed(1) + 'min';
            if (details.risk_score != null) desc += ' 风险:' + (details.risk_score > 1 ? details.risk_score.toFixed(0) : (details.risk_score * 100).toFixed(0)) + '%';
            return desc;
        } else {
            let desc = '海缆锚害风险';
            if (details.anchor_duration_min != null) desc += ' 停泊:' + details.anchor_duration_min.toFixed(1) + 'min';
            if (details.zone_id) desc += ' 区域:' + details.zone_id;
            return desc;
        }
    },

    updateAlertStats(stats) {
        StatsModule.renderStats(stats);
    },

    playAlertSound(level) {
        try {
            const ctx = new (window.AudioContext || window.webkitAudioContext)();
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.connect(gain);
            gain.connect(ctx.destination);

            const isLevel1 = level === 'level1_collision' || level === 'high' || level === 1;
            if (isLevel1) {
                osc.frequency.value = 880;
                gain.gain.value = 0.15;
                osc.start();
                osc.stop(ctx.currentTime + 0.3);
                setTimeout(() => {
                    const osc2 = ctx.createOscillator();
                    const gain2 = ctx.createGain();
                    osc2.connect(gain2);
                    gain2.connect(ctx.destination);
                    osc2.frequency.value = 880;
                    gain2.gain.value = 0.15;
                    osc2.start();
                    osc2.stop(ctx.currentTime + 0.3);
                }, 400);
            } else {
                osc.frequency.value = 660;
                gain.gain.value = 0.1;
                osc.start();
                osc.stop(ctx.currentTime + 0.2);
            }
        } catch (e) {
        }
    },

    formatTime(ts) {
        if (!ts) return '-';
        const d = new Date(ts);
        const pad = n => String(n).padStart(2, '0');
        return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
    }
};
