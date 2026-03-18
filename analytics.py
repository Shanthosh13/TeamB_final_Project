import streamlit as st
import pandas as pd
import plotly.express as px
from collections import defaultdict
import plotly.graph_objects as go

def render_dashboard(dataset):
    rows = dataset.get("recent", [])
    if not rows:
        st.info("No attempts yet. Submit a quiz to populate analytics.")
        return

    # Add custom CSS for metric cards
    st.markdown("""
        <style>
        .metric-card {
            background: linear-gradient(135deg, rgba(255,255,255,0.8), rgba(255,255,255,0.4));
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            border-radius: 20px;
            padding: 24px;
            box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.1);
            border: 1px solid rgba(255, 255, 255, 0.6);
            text-align: center;
            border-left: 8px solid #ff0844;
            transition: all 0.3s ease;
            margin-bottom: 24px;
        }
        .metric-card:hover {
            transform: translateY(-8px) scale(1.02);
            box-shadow: 0 15px 40px rgba(255, 8, 68, 0.2);
        }
        .metric-card h3 {
            margin: 0;
            color: #4a5568 !important;
            font-size: 1.1rem;
            font-weight: 700 !important;
            text-transform: uppercase;
            letter-spacing: 1px;
        }
        .metric-card h2 {
            margin: 12px 0 0 0;
            background: -webkit-linear-gradient(45deg, #ff0844, #ffb199);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            font-size: 3rem;
            font-weight: 900;
        }
        .chart-container {
            background: rgba(255, 255, 255, 0.6);
            backdrop-filter: blur(15px);
            border-radius: 20px;
            padding: 24px;
            box-shadow: 0 8px 32px rgba(31, 38, 135, 0.05);
            border: 1px solid rgba(255, 255, 255, 0.8);
            margin-bottom: 30px;
            transition: transform 0.3s;
        }
        .chart-container:hover {
            transform: translateY(-4px);
            box-shadow: 0 15px 40px rgba(31, 38, 135, 0.15);
        }
        </style>
    """, unsafe_allow_html=True)

    st.markdown("### 🏆 High-Level Overview")
    col1, col2, col3 = st.columns(3)
    
    total_attempts = len(rows)
    best_pct = round(max(dataset.get("percentages", [0])), 2) if dataset.get("percentages") else 0
    
    percentages = dataset.get("percentages", [])
    avg_pct = round(sum(percentages) / max(len(percentages), 1), 2)
    
    with col1:
        st.markdown(f'<div class="metric-card"><h3>Total Attempts</h3><h2>{total_attempts}</h2></div>', unsafe_allow_html=True)
    with col2:
        st.markdown(f'<div class="metric-card" style="border-left-color: #10b981;"><h3>Best Accuracy</h3><h2>{best_pct}%</h2></div>', unsafe_allow_html=True)
    with col3:
        st.markdown(f'<div class="metric-card" style="border-left-color: #f59e0b;"><h3>Average Accuracy</h3><h2>{avg_pct}%</h2></div>', unsafe_allow_html=True)

    # ---------------------------------------------------------
    # NEW STUDENT PERFORMANCE SECTION
    # ---------------------------------------------------------
    st.markdown("### 👨‍🎓 Student Performance Leaderboard")
    user_stats = defaultdict(lambda: {"score": 0, "total": 0, "attempts": 0})
    for r in rows:
        uname = r.get("user_name", "Unknown")
        user_stats[uname]["score"] += r.get("score", 0)
        user_stats[uname]["total"] += r.get("total", 0)
        user_stats[uname]["attempts"] += 1

    student_data = []
    for uname, stats in user_stats.items():
        rate = (stats["score"] / stats["total"] * 100) if stats["total"] > 0 else 0
        student_data.append({
            "Student": uname, 
            "Performance Rate (%)": round(rate, 2), 
            "Total Quizzes": stats["attempts"]
        })
    df_students = pd.DataFrame(student_data).sort_values("Performance Rate (%)", ascending=True)

    fig_student = px.bar(
        df_students, 
        x="Performance Rate (%)", 
        y="Student", 
        orientation="h",
        color="Performance Rate (%)",
        hover_data=["Total Quizzes"],
        color_continuous_scale="Viridis",
        title="Student Performance Rate & Leaderboard"
    )
    fig_student.update_layout(plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)", showlegend=False)

    st.markdown('<div class="chart-container">', unsafe_allow_html=True)
    st.plotly_chart(fig_student, use_container_width=True)
    st.markdown('</div>', unsafe_allow_html=True)

    # ---------------------------------------------------------
    # NEW DAILY ACTIVITY TIMELINE
    # ---------------------------------------------------------
    st.markdown("### 📅 Engagement Timeline")
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
            color_discrete_sequence=["#6366f1"]
        )
        fig_timeline.update_layout(plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
        
        st.markdown('<div class="chart-container">', unsafe_allow_html=True)
        st.plotly_chart(fig_timeline, use_container_width=True)
        st.markdown('</div>', unsafe_allow_html=True)
    
    # ---------------------------------------------------------
    # PREVIOUS CHARTS (Trend & Difficulty Breakdown)
    # ---------------------------------------------------------
    st.markdown("### 📈 Comprehensive Metrics")
    chart_col1, chart_col2 = st.columns(2)
    
    # Accuracy Trend
    progress_df = pd.DataFrame(
        {"Attempt": list(range(1, len(percentages) + 1)), "Accuracy": percentages}
    )
    fig_trend = px.line(progress_df, x="Attempt", y="Accuracy", markers=True, 
                        title="📉 Accuracy Trend Over Time", template="plotly_white")
    fig_trend.update_traces(line_color="#0e7490", marker=dict(size=10, color="#10b981", line=dict(width=2, color="white")))
    fig_trend.update_layout(plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
    
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
        color_discrete_sequence=["#3b82f6"]
    )
    fig_dist.update_layout(plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)", bargap=0.1)
    
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
                         color_discrete_sequence=px.colors.qualitative.Pastel)
        fig_bar.update_layout(showlegend=False, plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
                         
        with bar_col:
            st.markdown('<div class="chart-container">', unsafe_allow_html=True)
            st.plotly_chart(fig_bar, use_container_width=True)
            st.markdown('</div>', unsafe_allow_html=True)
            
        fig_radar = go.Figure(data=go.Scatterpolar(
            r=diff_df['Accuracy'].tolist() + [diff_df['Accuracy'].iloc[0]] if len(diff_df) > 0 else [],
            theta=diff_df['Difficulty'].tolist() + [diff_df['Difficulty'].iloc[0]] if len(diff_df) > 0 else [],
            fill='toself',
            marker=dict(color='#8b5cf6'),
            line=dict(color='#8b5cf6')
        ))
        fig_radar.update_layout(
            polar=dict(radialaxis=dict(visible=True, range=[0, 100])),
            showlegend=False,
            title="🕸️ Difficulty Strengths (Radar)",
            paper_bgcolor="rgba(0,0,0,0)"
        )
        
        with radar_col:
            st.markdown('<div class="chart-container">', unsafe_allow_html=True)
            st.plotly_chart(fig_radar, use_container_width=True)
            st.markdown('</div>', unsafe_allow_html=True)

    st.markdown("### 🕒 Recent Submissions")
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
