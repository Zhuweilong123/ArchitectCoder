"""Regenerate README SVG formulas; optionally pass --preview-dir for PNGs."""
from argparse import ArgumentParser
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

FORMULAS = {
    "node-cost": r"C(v)=\max\left(0.1,\ \frac{1}{5}\sum_{i=1}^{5}\frac{\ln(1+x_i(v))}{\ln(1+r_i)}\right)",
    "shared-file-cost": r"C(U)=\max_{v\in U}C(v)+0.35\left(\sum_{v\in U}C(v)-\max_{v\in U}C(v)\right)",
    "partition-objective": r"J(P)=\frac{\max_k L_k}{\max(\sum_k L_k,\ 0.1)}+0.45\frac{W_{\mathrm{cut}}(P)}{\max(W_{\mathrm{all}},\ 1)}",
}

def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--preview-dir", type=Path)
    args = parser.parse_args()
    output = Path(__file__).resolve().parent
    if args.preview_dir:
        args.preview_dir.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"svg.fonttype": "path", "svg.hashsalt": "architecture-scheduling"})
    for name, formula in FORMULAS.items():
        fig = plt.figure(figsize=(12, 2), facecolor="white")
        label = fig.text(0.5, 0.5, f"${formula}$", ha="center", va="center",
                         fontsize=25, color="#172033")
        fig.canvas.draw()
        bounds = label.get_window_extent().transformed(fig.dpi_scale_trans.inverted())
        bounds = bounds.expanded(1.08, 1.55)
        fig.savefig(output / f"{name}.svg", bbox_inches=bounds,
                    facecolor="white", metadata={"Date": None, "Description": formula})
        if args.preview_dir:
            fig.savefig(args.preview_dir / f"{name}.png", bbox_inches=bounds,
                        facecolor="white", dpi=160)
        plt.close(fig)

if __name__ == "__main__":
    main()
