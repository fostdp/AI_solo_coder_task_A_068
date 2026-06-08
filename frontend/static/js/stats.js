const StatsModule = {
    API_BASE: 'http://localhost:8000/api',

    loadMonthlyStats() {
        fetch(this.API_BASE + '/alerts/stats')
            .then(res => {
                if (!res.ok) return null;
                return res.json();
            })
            .then(data => {
                if (data) this.renderStats(data);
            })
            .catch(e => {
                console.error('Stats load error:', e);
            });
    },

    renderStats(stats) {
        const container = document.getElementById('statsCards');
        const chartCanvas = document.getElementById('statsChart');

        if (!stats) return;

        let total = 0, level1 = 0, level2 = 0;

        if (Array.isArray(stats)) {
            for (const s of stats) {
                const count = s.count || 0;
                total += count;
                if (s.level === 'level1_collision') level1 += count;
                else if (s.level === 'level2_cable') level2 += count;
            }
        } else {
            total = stats.total_alerts || 0;
            level1 = stats.level1_count || 0;
            level2 = stats.level2_count || 0;
        }

        container.innerHTML =
            '<div class="stat-card"><span class="stat-label">本月总预警</span><span class="stat-value blue">' + total + '</span></div>' +
            '<div class="stat-card"><span class="stat-label">碰撞预警(L1)</span><span class="stat-value red">' + level1 + '</span></div>' +
            '<div class="stat-card"><span class="stat-label">海缆预警(L2)</span><span class="stat-value orange">' + level2 + '</span></div>' +
            '<div class="stat-card"><span class="stat-label">高风险船舶</span><span class="stat-value">' + level1 + '</span></div>';

        const byDay = stats.by_day || stats.daily_breakdown;
        if (byDay) {
            const dailyData = this.convertByDayToChartData(byDay);
            if (dailyData.length > 0) {
                this.drawDailyChart(chartCanvas, dailyData);
            }
        }

        const byShipType = stats.by_ship_type || stats.ship_type_breakdown;
        if (byShipType) {
            this.renderShipTypeBreakdown(byShipType);
        }
    },

    convertByDayToChartData(byDay) {
        if (Array.isArray(byDay)) return byDay;
        const entries = Object.entries(byDay);
        return entries.map(([day, count]) => ({
            day: day.slice(-5),
            count: typeof count === 'object' ? (count.count || 0) : count,
            level: 1
        }));
    },

    drawDailyChart(canvas, dailyData) {
        if (!canvas) return;
        const ctx = canvas.getContext('2d');
        const w = canvas.width;
        const h = canvas.height;

        ctx.clearRect(0, 0, w, h);

        const max = Math.max(...dailyData.map(d => d.count || 0), 1);
        const barWidth = Math.max(2, (w - 30) / dailyData.length - 2);
        const chartH = h - 25;
        const startX = 25;

        ctx.fillStyle = '#7a8a9a';
        ctx.font = '9px sans-serif';
        ctx.fillText(max, 0, 12);

        for (let i = 0; i < dailyData.length; i++) {
            const d = dailyData[i];
            const barH = ((d.count || 0) / max) * (chartH - 10);
            const x = startX + i * (barWidth + 2);
            const y = chartH - barH;

            ctx.fillStyle = d.level === 1 ? 'rgba(244,67,54,0.7)' : 'rgba(255,152,0,0.7)';
            ctx.fillRect(x, y, barWidth, barH);

            if (i % 5 === 0 || dailyData.length <= 10) {
                ctx.fillStyle = '#556677';
                ctx.font = '7px sans-serif';
                ctx.fillText(d.day || '', x, chartH + 10);
            }
        }

        ctx.strokeStyle = '#1e2a3a';
        ctx.lineWidth = 0.5;
        ctx.beginPath();
        ctx.moveTo(startX, chartH);
        ctx.lineTo(w, chartH);
        ctx.stroke();
    },

    renderShipTypeBreakdown(breakdown) {
        const container = document.getElementById('statsCards');
        if (!container || !breakdown) return;

        let entries;
        if (Array.isArray(breakdown)) {
            entries = breakdown.map(b => [b._id || b.type, b.count]);
        } else {
            entries = Object.entries(breakdown);
        }

        if (entries.length === 0) return;

        const typeNames = {
            cargo: '货船',
            tanker: '油轮',
            passenger: '客船',
            fishing: '渔船',
            tug: '拖船',
            other: '其他'
        };

        let html = '<div style="grid-column:1/-1;margin-top:8px;font-size:10px;color:#7a8a9a;">船舶类型分布:</div>';
        for (const [type, count] of entries) {
            html += '<div class="stat-card" style="padding:5px 8px;">' +
                '<span class="stat-label">' + (typeNames[type] || type) + '</span>' +
                '<span class="stat-value" style="font-size:14px;">' + count + '</span>' +
                '</div>';
        }

        container.innerHTML += html;
    }
};
