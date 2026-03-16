import pandas as pd
import plotly.express as px


def build_progress_chart(scores):
    if not scores:
        return None

    frame = pd.DataFrame(
        {
            "Attempt": list(range(1, len(scores) + 1)),
            "Score": scores,
        }
    )
    return px.line(
        frame,
        x="Attempt",
        y="Score",
        markers=True,
        title="Performance Trend",
        template="plotly_white",
    )


def build_accuracy_distribution(attempt_rows):
    if not attempt_rows:
        return None

    frame = pd.DataFrame(attempt_rows)
    return px.histogram(
        frame,
        x="percentage",
        nbins=10,
        title="Accuracy Distribution",
        labels={"percentage": "Accuracy (%)"},
        template="plotly_white",
    )
