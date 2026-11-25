import numpy as np
import pandas as pd
import streamlit as st
import matplotlib.pyplot as plt
from pypower.api import runopf, ppoption
from pypower.idx_bus import BUS_I, PD, QD, LAM_P
from pypower.idx_gen import GEN_BUS, PG, QG

st.set_page_config(page_title="Six-Bus OPF & LMP", page_icon="⚡", layout="wide")
st.title("⚡ Six-Bus AC-OPF and LMP Calculator")
st.write("Enter your network loads and branch parameters, then run AC-OPF.")

st.subheader("Generator offers (from your Table II)")
offers_df = pd.DataFrame([
    [1, "Grid", 10.0, 2.0],
    [4, "DER-1", 0.5, 1.0],
    [5, "DER-2", 2.5, 3.0],
    [6, "DER-3", 0.5, 1.0],
], columns=["Bus", "Generator", "Pmax (MW)", "Offer ($/MWh)"])
st.dataframe(offers_df, use_container_width=True, hide_index=True)

c1, c2, c3 = st.columns(3)
base_mva = c1.number_input("System base (MVA)", min_value=0.001, value=1.0)
base_kv = c2.number_input("Base voltage (kV)", min_value=0.001, value=11.0)
topology = c3.selectbox("Network", ["Radial", "Meshed"])

st.subheader("1. Load data")
st.caption("D1 is at Bus 2, D2 at Bus 3, D3 at Bus 4, D4 at Bus 5, D5 at Bus 6.")
loads_default = pd.DataFrame({
    "Bus": [2, 3, 4, 5, 6],
    "Load": ["D1", "D2", "D3", "D4", "D5"],
    "P (MW)": [0.0]*5,
    "Power factor": [1.0]*5
})
loads = st.data_editor(
    loads_default, hide_index=True, use_container_width=True, num_rows="fixed",
    column_config={
        "Bus": st.column_config.NumberColumn(disabled=True),
        "Load": st.column_config.TextColumn(disabled=True),
        "P (MW)": st.column_config.NumberColumn(min_value=0.0, step=0.01),
        "Power factor": st.column_config.NumberColumn(min_value=-1.0, max_value=1.0, step=0.01)
    }, key="loads"
)

st.subheader("2. Branch data")
st.caption("Connections below are a tentative reading of your diagram. Verify the topology against your paper.")
radial = [(1,2), (2,3), (3,4), (2,5), (5,6)]
meshed = radial + [(3,6), (4,6)]
connections = radial if topology == "Radial" else meshed
branch_default = pd.DataFrame([
    {"From bus": a, "To bus": b, "R (p.u.)": 0.0, "X (p.u.)": 0.0, "Rating (MVA)": 0.0}
    for a,b in connections
])
branches = st.data_editor(
    branch_default, hide_index=True, use_container_width=True, num_rows="fixed",
    column_config={
        "From bus": st.column_config.NumberColumn(disabled=True),
        "To bus": st.column_config.NumberColumn(disabled=True),
        "R (p.u.)": st.column_config.NumberColumn(min_value=0.0, step=0.001),
        "X (p.u.)": st.column_config.NumberColumn(min_value=0.000001, step=0.001),
        "Rating (MVA)": st.column_config.NumberColumn(min_value=0.000001, step=0.1)
    }, key=f"branches_{topology}"
)

with st.expander("Advanced assumptions (change to match your paper)"):
    qmax = st.number_input("Generator Qmax (MVAr)", value=10.0, min_value=0.0)
    qmin_mag = st.number_input("Magnitude of generator Qmin (MVAr)", value=10.0, min_value=0.0)
    vmin = st.number_input("Minimum voltage (p.u.)", value=0.95, min_value=0.5, max_value=1.0)
    vmax = st.number_input("Maximum voltage (p.u.)", value=1.05, min_value=1.0, max_value=1.5)

st.caption("Generator reactive limits and voltage limits are assumptions unless specified by your paper.")
if st.button("Run AC-OPF and calculate LMPs", type="primary", use_container_width=True):
    errors = []
    if (loads["P (MW)"] < 0).any():
        errors.append("Loads must be non-negative.")
    if ((loads["Power factor"] == 0) | (loads["Power factor"].abs() > 1)).any():
        errors.append("Power factors must be between -1 and 1, excluding zero.")
    if (branches["X (p.u.)"] <= 0).any():
        errors.append("Enter positive reactance for every line.")
    if (branches["Rating (MVA)"] <= 0).any():
        errors.append("Enter positive MVA rating for every line.")
    if (branches["R (p.u.)"] < 0).any():
        errors.append("Resistance cannot be negative.")
    if vmax <= vmin:
        errors.append("Vmax must be greater than Vmin.")
    if errors:
        for msg in errors:
            st.error(msg)
    else:
        try:
            ppc = {"version": "2", "baseMVA": float(base_mva)}
            load_map = {}
            for _, row in loads.iterrows():
                p = float(row["P (MW)"])
                pf = float(row["Power factor"])
                q = p * np.tan(np.arccos(abs(pf)))
                if pf < 0:
                    q = -q  # negative PF represents leading load in this app
                load_map[int(row["Bus"])] = (p, q)

            bus_rows = []
            for bus in range(1, 7):
                btype = 3 if bus == 1 else (2 if bus in [4,5,6] else 1)
                pd_mw, qd = load_map.get(bus, (0.0, 0.0))
                bus_rows.append([bus, btype, pd_mw, qd, 0, 0, 1, 1.0, 0.0,
                                 float(base_kv), 1, float(vmax), float(vmin)])
            ppc["bus"] = np.array(bus_rows, dtype=float)

            gen_offers = [(1,10.0,2.0), (4,0.5,1.0), (5,2.5,3.0), (6,0.5,1.0)]
            ppc["gen"] = np.array([
                [bus, 0, 0, qmax, -qmin_mag, 1.0, base_mva, 1, pmax, 0]
                for bus,pmax,price in gen_offers
            ], dtype=float)
            ppc["gencost"] = np.array([
                [2, 0, 0, 2, price, 0] for bus,pmax,price in gen_offers
            ], dtype=float)

            branch_rows = []
            for _, row in branches.iterrows():
                rate = float(row["Rating (MVA)"])
                branch_rows.append([
                    int(row["From bus"]), int(row["To bus"]),
                    float(row["R (p.u.)"]), float(row["X (p.u.)"]), 0.0,
                    rate, rate, rate, 0.0, 0.0, 1, -360.0, 360.0
                ])
            ppc["branch"] = np.array(branch_rows, dtype=float)

            with st.spinner("Solving AC-OPF..."):
                result = runopf(ppc, ppoption(VERBOSE=0, OUT_ALL=0))

            if not result["success"]:
                st.error("AC-OPF did not converge. Check loads, line parameters, ratings, generator limits and voltage limits.")
            else:
                st.success("AC-OPF converged successfully.")
                lmp = pd.DataFrame({
                    "Bus": result["bus"][:, BUS_I].astype(int),
                    "Active load (MW)": result["bus"][:, PD],
                    "Reactive load (MVAr)": result["bus"][:, QD],
                    "LMP ($/MWh)": result["bus"][:, LAM_P]
                })
                gen = pd.DataFrame({
                    "Generator": ["Grid", "DER-1", "DER-2", "DER-3"],
                    "Bus": result["gen"][:, GEN_BUS].astype(int),
                    "Cleared generation (MW)": result["gen"][:, PG],
                    "Reactive generation (MVAr)": result["gen"][:, QG],
                    "Offer ($/MWh)": [2.0, 1.0, 3.0, 1.0]
                })
                st.subheader("3. Bus LMP results")
                st.dataframe(lmp.round(4), use_container_width=True, hide_index=True)
                st.download_button("Download LMP CSV", lmp.to_csv(index=False), "six_bus_lmp.csv", "text/csv")
                st.subheader("4. Generator dispatch")
                st.dataframe(gen.round(4), use_container_width=True, hide_index=True)
                st.download_button("Download dispatch CSV", gen.to_csv(index=False), "generator_dispatch.csv", "text/csv")
                a,b = st.columns(2)
                a.metric("Total generation (MW)", f"{result['gen'][:, PG].sum():.4f}")
                b.metric("Total load (MW)", f"{result['bus'][:, PD].sum():.4f}")
                fig, ax = plt.subplots()
                ax.plot(lmp["Bus"], lmp["LMP ($/MWh)"], marker="o")
                ax.set_xlabel("Bus")
                ax.set_ylabel("LMP ($/MWh)")
                ax.set_title(f"{topology} system LMP")
                ax.set_xticks(range(1,7))
                ax.grid(True)
                st.pyplot(fig)
        except Exception as exc:
            st.exception(exc)

st.divider()
st.caption("This app uses PYPOWER AC-OPF and linear generator offer curves. Exact reproduction of your paper requires its exact loads, branch impedances, ratings, generator limits, topology and OPF formulation.")
