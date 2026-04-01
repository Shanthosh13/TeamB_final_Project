import streamlit as st
import pandas as pd
import plotly.express as px
from collections import defaultdict
import plotly.graph_objects as go


CHART_THEME = {
    "paper_bgcolor": "#ffffff",
    "plot_bgcolor": "#f8fbfd",
    "font": {"color": "#13222d", "family": "Plus Jakarta Sans, Segoe UI, sans-serif", "size": 13},
}


def style_figure(fig):
    fig.update_layout(
        **CHART_THEME,
        margin=dict(l=40, r=20, t=58, b=36),
        title_font=dict(size=18, color="#10222d"),
        xaxis=dict(
            showgrid=True,
            gridcolor="#e4edf2",
            zeroline=False,
            linecolor="#d4e0e8",
            tickfont=dict(color="#344957"),
            title_font=dict(color="#344957"),
        ),
        yaxis=dict(
            showgrid=True,
            gridcolor="#e4edf2",
            zeroline=False,
            linecolor="#d4e0e8",
            tickfont=dict(color="#344957"),
            title_font=dict(color="#344957"),
        ),
        legend=dict(bgcolor="rgba(255,255,255,0.8)", bordercolor="#d9e3ea", borderwidth=1),
    )
    return fig


def render_dashboard(dataset):
    rows = dataset.get("recent", [])
    if not rows:
        st.info("No attempts yet. Submit a quiz to populate analytics.")
        return

    # User Interactivity: Date Filter
    st.sidebar.markdown("### 🔍 Filter Analytics")
    df_temp = pd.DataFrame(rows)
    if not df_temp.empty and "submitted_at" in df_temp.columns:
        df_temp["submitted_at"] = pd.to_datetime(df_temp["submitted_at"])
        min_date = df_temp["submitted_at"].min().date()
        max_date = df_temp["submitted_at"].max().date()
        date_range = st.sidebar.date_input("Date Range", [min_date, max_date], min_value=min_date, max_value=max_date)
        
        if len(date_range) == 2:
            start_date, end_date = date_range
            mask = (df_temp["submitted_at"].dt.date >= start_date) & (df_temp["submitted_at"].dt.date <= end_date)
            rows = df_temp.loc[mask].to_dict('records')
            if not rows:
                st.warning("No data for the selected period.")
                return

    # Dashboard-scoped CSS for cards, tables, and chart wrappers.
    st.markdown("""
        <style>
        .analytics-title {
            font-size: 1.4rem;
            font-family: 'Fraunces', serif;
            font-weight: 700;
            color: #10202a;
            margin: 1.5rem 0 1rem;
            letter-spacing: -0.2px;
        }
        .metric-card {
            background: rgba(255, 255, 255, 0.7);
            backdrop-filter: blur(10px);
            border-radius: 20px;
            padding: 22px 20px;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.03);
            border: 1px solid rgba(255, 255, 255, 0.8);
            text-align: left;
            transition: all 0.3s ease;
            margin-bottom: 15px;
            position: relative;
            overflow: hidden;
        }
        .metric-card::after {
            content: '';
            position: absolute;
            right: -10px; bottom: -10px;
            width: 80px; height: 80px;
            background: radial-gradient(circle, rgba(0, 109, 119, 0.05) 0%, transparent 70%);
            border-radius: 50%;
        }
        .metric-card:hover {
            transform: translateY(-5px);
            box-shadow: 0 15px 35px rgba(0, 109, 119, 0.1);
            background: rgba(255, 255, 255, 0.9);
        }
        .metric-card h3 {
            margin: 0;
            color: #4a5f6b !important;
            font-size: 0.85rem;
            font-family: 'Plus Jakarta Sans', sans-serif;
            font-weight: 600 !important;
            text-transform: uppercase;
            letter-spacing: 1px;
        }
        .metric-card h2 {
            margin: 10px 0 0 0;
            color: #006d77;
            font-size: 2.3rem;
            font-family: 'Fraunces', serif;
            font-weight: 700;
        }
        .chart-container {
            background: rgba(255, 255, 255, 0.6);
            backdrop-filter: blur(8px);
            border-radius: 24px;
            padding: 20px;
            box-shadow: 0 12px 40px rgba(0, 0, 0, 0.04);
            border: 1px solid rgba(255, 255, 255, 0.5);
            margin-bottom: 25px;
        }

        [data-testid="stDataFrame"] {
            border: 1px solid #dbe6ee;
            border-radius: 14px;
            overflow: hidden;
            box-shadow: 0 8px 22px rgba(16, 34, 45, 0.07);
        }

        @media (max-width: 900px) {
            .metric-card {
                padding: 14px;
                margin-bottom: 8px;
            }
            .metric-card h2 {
                font-size: 1.7rem;
            }
            .chart-container {
                padding: 8px 8px 0;
            }
        }
        </style>
    """, unsafe_allow_html=True)

    st.markdown('<div class="analytics-title">🏆 High-Level Overview</div>', unsafe_allow_html=True)
    
    # Download Button for the whole dataset
    df_all = pd.DataFrame(rows)
    if not df_all.empty:
        csv_data = df_all.to_csv(index=False).encode('utf-8')
        st.sidebar.download_button(
            "📥 Download Analytics CSV",
            csv_data,
            "all_attempts.csv",
            "text/csv",
            key='download-analytics-csv'
        )
    
    col1, col2, col3 = st.columns(3)
    
    total_attempts = len(rows)
    best_pct = round(max(dataset.get("percentages", [0])), 2) if dataset.get("percentages") else 0
    
    percentages = dataset.get("percentages", [])
    avg_pct = round(sum(percentages) / max(len(percentages), 1), 2)
    
    with col1:
        st.markdown(
            f'<div class="metric-card"><h3>Total Attempts</h3><h2>{total_attempts}</h2></div>',
            unsafe_allow_html=True,
        )
    with col2:
        st.markdown(
            f'<div class="metric-card"><h3>Best Accuracy</h3><h2>{best_pct}%</h2></div>',
            unsafe_allow_html=True,
        )
    with col3:
        st.markdown(
            f'<div class="metric-card"><h3>Average Accuracy</h3><h2>{avg_pct}%</h2></div>',
            unsafe_allow_html=True,
        )

    # ---------------------------------------------------------
    # NEW DAILY ACTIVITY TIMELINE
    # ---------------------------------------------------------
    st.markdown('<div class="analytics-title">📅 Engagement Timeline</div>', unsafe_allow_html=True)
    df_rows = pd.DataFrame(rows)
    if "submitted_at" in df_rows.columns:
        df_rows["Date"] = pd.to_datetime(df_rows["submitted_at"]).dt.date
        date_counts = df_rows.groupby("Date").size().reset_index(name="Quizzes Taken")
        
        fig_timeline = px.area(
            date_counts, 
            x="Date", 
            y="Quizzes Taken", 
            markers=True, 
            title="Daily Quiz Activity", 
            template="plotly_white",
            color_discrete_sequence=["#0a9396"]
        )
        fig_timeline.update_traces(line=dict(width=3), marker=dict(size=7), fillcolor="rgba(10,147,150,0.18)")
        style_figure(fig_timeline)
        
        st.markdown('<div class="chart-container">', unsafe_allow_html=True)
        st.plotly_chart(fig_timeline, use_container_width=True)
        st.markdown('</div>', unsafe_allow_html=True)
    
    # ---------------------------------------------------------
    # PREVIOUS CHARTS (Trend & Difficulty Breakdown)
    # ---------------------------------------------------------
    st.markdown('<div class="analytics-title">📈 Comprehensive Metrics</div>', unsafe_allow_html=True)
    chart_col1, chart_col2 = st.columns(2)
    
    # Accuracy Trend
    progress_df = pd.DataFrame(
        {"Attempt": list(range(1, len(percentages) + 1)), "Accuracy": percentages}
    )
    fig_trend = px.line(progress_df, x="Attempt", y="Accuracy", markers=True, 
                        title="📉 Accuracy Trend Over Time", template="plotly_white")
    fig_trend.update_traces(
        line_color="#0b7285",
        line_width=3,
        marker=dict(size=9, color="#0a9396", line=dict(width=2, color="#ffffff")),
    )
    style_figure(fig_trend)
    
    with chart_col1:
        st.markdown('<div class="chart-container">', unsafe_allow_html=True)
        st.plotly_chart(fig_trend, use_container_width=True)
        st.markdown('</div>', unsafe_allow_html=True)

    # Score Distribution
    fig_dist = px.histogram(
        df_rows,
        x="percentage",
        nbins=10,
        title="📊 Score Percentage Distribution",
        labels={"percentage": "Accuracy (%)"},
        template="plotly_white",
        color_discrete_sequence=["#2f80ed"]
    )
    fig_dist.update_layout(bargap=0.12)
    fig_dist.update_traces(marker_line_color="#163548", marker_line_width=0.6)
    style_figure(fig_dist)
    
    with chart_col2:
        st.markdown('<div class="chart-container">', unsafe_allow_html=True)
        st.plotly_chart(fig_dist, use_container_width=True)
        st.markdown('</div>', unsafe_allow_html=True)

    # Difficulty Breakdown
    breakdown = defaultdict(lambda: {"correct": 0, "total": 0})
    for row in rows:
        for diff, values in row.get("difficulty_breakdown", {}).items():
            breakdown[diff]["correct"] += values.get("correct", 0)
            breakdown[diff]["total"] += values.get("total", 0)

    if breakdown:
        diff_rows = []
        for diff, values in breakdown.items():
            accuracy = (values["correct"] / values["total"] * 100) if values["total"] else 0
            diff_rows.append({"Difficulty": diff.title(), "Accuracy": round(accuracy, 2)})
        diff_df = pd.DataFrame(diff_rows)
        
        radar_col, bar_col = st.columns(2)
        
        fig_bar = px.bar(diff_df, x="Difficulty", y="Accuracy", color="Difficulty", 
                         title="🎯 Accuracy by Difficulty",
                         color_discrete_sequence=["#7ec8cf", "#2f80ed", "#0b7285"])
        fig_bar.update_layout(showlegend=False)
        fig_bar.update_traces(marker_line_color="#1a3e50", marker_line_width=0.6)
        style_figure(fig_bar)
                         
        with bar_col:
            st.markdown('<div class="chart-container">', unsafe_allow_html=True)
            st.plotly_chart(fig_bar, use_container_width=True)
            st.markdown('</div>', unsafe_allow_html=True)
            
        fig_radar = go.Figure(data=go.Scatterpolar(
            r=diff_df['Accuracy'].tolist() + [diff_df['Accuracy'].iloc[0]] if len(diff_df) > 0 else [],
            theta=diff_df['Difficulty'].tolist() + [diff_df['Difficulty'].iloc[0]] if len(diff_df) > 0 else [],
            fill='toself',
            marker=dict(color='#0a9396'),
            line=dict(color='#0b7285', width=2),
            fillcolor='rgba(10,147,150,0.25)'
        ))
        fig_radar.update_layout(
            polar=dict(
                bgcolor="#f8fbfd",
                radialaxis=dict(
                    visible=True,
                    range=[0, 100],
                    gridcolor="#dbe6ee",
                    linecolor="#dbe6ee",
                    tickfont=dict(color="#344957"),
                ),
                angularaxis=dict(
                    gridcolor="#dbe6ee",
                    linecolor="#dbe6ee",
                    tickfont=dict(color="#344957"),
                ),
            ),
            showlegend=False,
            title="🕸️ Difficulty Strengths (Radar)",
            paper_bgcolor="#ffffff",
            margin=dict(l=40, r=20, t=58, b=36),
            font=dict(color="#13222d", family="Plus Jakarta Sans, Segoe UI, sans-serif"),
        )
        
        with radar_col:
            st.markdown('<div class="chart-container">', unsafe_allow_html=True)
            st.plotly_chart(fig_radar, use_container_width=True)
            st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="analytics-title">🕒 Recent Submissions</div>', unsafe_allow_html=True)
    table = pd.DataFrame(rows)[["user_name", "score", "total", "percentage", "submitted_at"]]
    table = table.rename(
        columns={
            "user_name": "User",
            "score": "Score",
            "total": "Total",
            "percentage": "Accuracy %",
            "submitted_at": "Submitted At (UTC)",
        }
    )
    
    st.dataframe(table, use_container_width=True, hide_index=True)