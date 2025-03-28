import streamlit as st
import logging
import requests
import json
import pytz
import pandas as pd
import altair as alt
from datetime import datetime, timedelta

from drive_handler import DriveManager

# Constants
GMT7 = pytz.timezone("Asia/Bangkok")
UTC = pytz.utc
THINGSPEAK_URL = "https://api.thingspeak.com/channels/2652379/feeds.json"
COMBINED_FILENAME = "combined_data.csv"
COMBINED_ID = "1-2egCgGVsVMPcRrqjvcF0LCSMk1Q1KO0"

def convert_utc_to_GMT7(timestamp):
    """Convert UTC timestamp to GMT+7."""
    return timestamp.replace(tzinfo=UTC).astimezone(GMT7)

# Data Retrieval from Google Drive
@st.cache_data(ttl=86400)
def combined_data_retrieve():
    drive_handler = DriveManager(st.secrets["SERVICE_ACCOUNT"])
    df = drive_handler.read_csv_file(COMBINED_ID)
    df["Timestamp (GMT+7)"] = pd.to_datetime(df["Timestamp (GMT+7)"], utc=True).dt.tz_convert("Asia/Bangkok")
    return df

# Thingspeak API Data Retrieval
def fetch_thingspeak_data(results):
    url = f"{THINGSPEAK_URL}?results={results}"
    response = requests.get(url)
    if response.status_code == 200:
        return json.loads(response.text)["feeds"]
    else:
        st.error("Failed to fetch data from Thingspeak API")
        return []

def append_new_data(df, feeds):
    """Append new data from Thingspeak API to DataFrame."""
    last_timestamp = df.iloc[-1, 0]

    for feed in feeds:
        timestamp = feed.get('created_at', '')
        if timestamp:
            utc_time = datetime.strptime(timestamp, r"%Y-%m-%dT%H:%M:%SZ")
            gmt7_time = convert_utc_to_GMT7(utc_time)

            if gmt7_time > last_timestamp:
                df.loc[len(df)] = [
                    gmt7_time,
                    float(feed.get("field1", 0)),
                    float(feed.get("field2", 0)),
                    int(feed.get("field3", 0)),
                    float(feed.get("field4", 0)),
                    float(feed.get("field5", 0))
                ]

    df["Timestamp (GMT+7)"] = pd.to_datetime(df["Timestamp (GMT+7)"], utc=True).dt.tz_convert("Asia/Bangkok")
    return df

def thingspeak_retrieve(df):
    today = datetime.now(GMT7).date()
    date_diff = (today - df.iloc[-1, 0].date()).days
    results = 150 * date_diff

    feeds = fetch_thingspeak_data(results)
    return append_new_data(df, feeds)

# Sidebar Input Features
def sidebar_inputs(df):
    col_names = [col for col in df.columns if col != "Timestamp (GMT+7)"]
    selected_cols = st.sidebar.multiselect("Columns to display in detail", col_names, [name for name in col_names if name not in ["DO Value", "DO Temperature"]])
    selected_cols.insert(0, "Timestamp (GMT+7)")

    target_col = st.sidebar.selectbox("Choose a column to analyze:", [col for col in selected_cols if col != 'Timestamp (GMT+7)'], index=0)

    min_date = datetime(2025, 1, 17).date()  # Fixed first date
    max_date = datetime.now(GMT7).date()  # Today's date

    # Initialize session state variables
    if "date_from" not in st.session_state:
        st.session_state.date_from = max_date
    if "date_to" not in st.session_state:
        st.session_state.date_to = max_date

    # Sidebar buttons
    col1, col2 = st.sidebar.columns(2)
    if col1.button("First Day"):
        st.session_state.date_from = min_date  
    if col2.button("Today"):
        st.session_state.date_from, st.session_state.date_to = max_date, max_date

    # Date input fields
    date_from = st.sidebar.date_input("Date from:", min_value=min_date, max_value=max_date, value=st.session_state.date_from)
    date_to = st.sidebar.date_input("Date to:", min_value=min_date, max_value=max_date, value=st.session_state.date_to)

    # Resampling options
    resample_freq = st.sidebar.selectbox("Resample Frequency:", ["None", "Hour", "Day", "Month"], index=0)
    agg_function = st.sidebar.selectbox("Aggregation Function:", ["Min", "Max", "Median"], index=0)

    return selected_cols, date_from, date_to, target_col, resample_freq, agg_function

# Data Filtering
def filter_data(df, date_from, date_to, selected_cols):
    # Filter data by selected date range
    filtered_df = df[(df["Timestamp (GMT+7)"].dt.date >= date_from) & 
                     (df["Timestamp (GMT+7)"].dt.date <= date_to)].copy()

    return filtered_df[selected_cols]

def apply_aggregation(df, selected_cols, target_col, resample_freq, agg_function):
    if resample_freq == "None":
        return df  # No resampling, return original dataframe

    rule_map = {"Hour": "H", "Day": "D", "Month": "M"}
    agg_map = {"Min": "min", "Max": "max", "Median": "median"}

    if resample_freq not in rule_map or agg_function not in agg_map:
        st.error("Invalid resampling frequency or aggregation function.")
        return df

    # Convert timestamp to index for resampling
    df_resampled = df.set_index("Timestamp (GMT+7)")

    if agg_function in ["Min", "Max"]:
        idx_func = "idxmin" if agg_function == "Min" else "idxmax"

        # Group by selected time period
        grouped = df_resampled.groupby(pd.Grouper(freq=rule_map[resample_freq]))[target_col]

        # Remove empty groups before applying idxmin/idxmax
        idx = grouped.apply(lambda x: getattr(x, idx_func)() if not x.empty else None).dropna()

        # Retrieve actual timestamps where min/max occurred
        df_resampled = df_resampled.loc[idx].reset_index()

    elif agg_function == "Median":
        df_resampled = df_resampled.groupby(pd.Grouper(freq=rule_map[resample_freq]))[selected_cols].median().reset_index()

    return df_resampled

def plot_line_chart(df, col):
    # Ensure column exists
    if col not in df.columns:
        st.error(f"Column '{col}' not found in DataFrame.")
        return

    # Create a copy of the DataFrame to avoid modifying the original
    df_filtered = df.copy()

    # Convert Timestamp to string format for detailed hover
    df_filtered["Timestamp (UTC+7)"] = df_filtered["Timestamp (GMT+7)"].dt.strftime(r"%Y-%m-%d %H:%M:%S")

    # Altair Chart
    chart = (
        alt.Chart(df_filtered)
        .mark_line(point=True)  # Add points to the line
        .encode(
            x=alt.X("Timestamp (GMT+7):T", title="Timestamp"),
            y=alt.Y(f"{col}:Q", title="Value"),
            tooltip=[alt.Tooltip("Timestamp (UTC+7):N", title="Time"),  # Explicitly set as Nominal
                 alt.Tooltip(f"{col}:Q", title="Value")],
        )
        .interactive()  # Enable zooming & panning
    )

    # Display the chart in Streamlit
    st.altair_chart(chart, use_container_width=True)

# Streamlit Layout
def app():
    st.set_page_config(page_title="BASWAP-APP", page_icon="💧", layout="wide")
    st.title("BASWAP APP")

    st.markdown("""
    This app retrieves the water quality from a buoy-based monitoring system in Vinh Long, Vietnam.
    * **Data source:** [Thingspeak](https://thingspeak.mathworks.com/channels/2652379).
    """)

    placeholder = st.empty()
    with placeholder.container():
        df = combined_data_retrieve()
        df = thingspeak_retrieve(df)

        selected_cols, date_from, date_to, target_col, resample_freq, agg_function = sidebar_inputs(df)
        filtered_df = filter_data(df, date_from, date_to, selected_cols)

        col1, col2 = st.columns((1.5, 4), gap='medium')

        aggregated_df = apply_aggregation(filtered_df, selected_cols, target_col, resample_freq, agg_function)

        with col1:
            st.subheader('📊 Statistics')
            st.metric(label="Maximum", value=f"{aggregated_df[target_col].max():.2f}")
            st.metric(label="Minimum", value=f"{aggregated_df[target_col].min():.2f}")
            st.metric(label="Average", value=f"{aggregated_df[target_col].mean():.2f}")
            st.metric(label="Std Dev", value=f"{aggregated_df[target_col].std():.2f}")

        with col2:
            st.subheader("📈 Water Quality Graph")
            plot_line_chart(aggregated_df, target_col)

    # Show only the data points plotted in the graph but with all selected columns
    detailed_df = filtered_df[filtered_df["Timestamp (GMT+7)"].isin(aggregated_df["Timestamp (GMT+7)"])].reset_index(drop=True)

    st.subheader("🔍 Data Table")
    st.write(f"Data Dimension: {detailed_df.shape[0]} rows and {detailed_df.shape[1]} columns.")
    st.dataframe(detailed_df, use_container_width=True)

    st.button("Clear Cache", help="This clears all cached data, ensuring the app fetches the latest available information.", on_click=st.cache_data.clear)


if __name__ == "__main__":
    app()

