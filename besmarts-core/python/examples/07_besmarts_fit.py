"""
examples/besmarts_fit.py

This performs automated chemical perception starting from OpenFF Sage 2.1. For
expedience, only b4 is targeted and only the lengths are fit. The resulting
force field was fit on positions and gradients of a single molecule.
"""

import tempfile
import os
from typing import Dict, List
from besmarts.mechanics import fits
from besmarts.mechanics import smirnoff_models
from besmarts.mechanics import molecular_models as mm
from besmarts.core import graphs
from besmarts.core import topology
from besmarts.core import perception
from besmarts.core import arrays
from besmarts.core import assignments
from besmarts.assign import hierarchy_assign_rdkit
from besmarts.codecs import codec_rdkit
from besmarts.core import configs

configs.processors = 1
configs.remote_compute_enable = False
configs.workqueue_port = 54321

smi = "[C:1]1([H:9])=[C:2]([H:10])[C:3]([H:11])=[C:4]([C:5](=[O:6])[Cl:7])[O:8]1"

xyz_positions = """11

  C -1.44819400 -0.84940800  0.16848900
  C -1.59401300  0.50318700 -0.01678100
  C -0.27397600  1.02622600 -0.13503500
  C  0.58064400 -0.04716400 -0.01303100
  C  2.03461200 -0.06860900 -0.05925200
  O  2.72809700  0.90108700 -0.21909900
 Cl  2.76214600 -1.70734100  0.14655600
  O -0.13897300 -1.20044600  0.17351800
  H -2.15226800 -1.65836100  0.30609000
  H -2.52743000  1.04809900 -0.06180000
  H  0.02935200  2.05273200 -0.28965800
"""

xyz_grad = """11

  C      0.49755    0.17370   -0.04115
  C     -0.00884    0.07632   -0.01031
  C      0.20074   -0.69547    0.09073
  C     -0.02955    1.24848   -0.17483
  C      0.55229   -1.91119    0.25039
  O     -0.15948    0.65794   -0.08724
 Cl     -0.33030    0.82983   -0.10559
  O     -0.73720   -0.66864    0.11909
  H     -0.11502    0.11021   -0.01168
  H     -0.00691    0.04737   -0.00649
  H      0.02566   -0.05163    0.00657
"""


def new_gdb() -> assignments.graph_db:

    gcd = codec_rdkit.graph_codec_rdkit()
    gdb = assignments.graph_db()

    pos = assignments.xyz_to_graph_assignment(gcd, smi, xyz_positions)
    gx = assignments.xyz_to_graph_assignment(gcd, smi, xyz_grad)

    eid, gid = assignments.graph_db_add_single_molecule_state(
        gdb,
        pos,
        gradients=gx
    )

    return gdb


def run():
    """
    Here is the outline:
        1. Build the dataset and FF
        2. Configure the fitting strategy
        3. Configure the objective tiers
        4. Optimize
    """

    # == 1. Build the dataset and FF == #
    gdb = new_gdb()

    csys = load_sage_csys()

    # Parameterize everything in the graph db
    psys = fits.gdb_to_physical_systems(gdb, csys)

    # == 2. Configure the fitting strategy == #

    # Split on model 0, only b4
    models = {0: ["b4"]}
    strat = fits.forcefield_optimization_strategy_default(csys, models=models)
    co = fits.chemical_objective

    # == 3. Configure the objective tiers == #
    final = fits.objective_tier()
    final.objectives = {
        # A position objective for EID 0 (ethane). Performs a geometry
        # optimization and calculates the sum of squared error (SSE). The root
        # of the mean SSE is the RMSD
        0: fits.objective_config_position(
                assignments.graph_db_address(
                    eid=[0],
                ),
                scale=1
        ),

        # A gradient objective for EID 0 (ethane). The geometry is held fixed
        # at the reference, and calculates the objective as the SSE of the
        # difference in QM/MM forces
        1: fits.objective_config_gradient(
                assignments.graph_db_address(
                    eid=[0],
                ),
                scale=1e-9
        ),
    }

    # We optimize parameters only model 0 (bonds).
    fit_models = [0]
    final.fit_models = fit_models
    # We optimize only on lengths (of model 0)
    final.fit_symbols = ["l"]

    tier = fits.objective_tier()
    tier.objectives = final.objectives
    # For the scoring tier, perform 2 FF optimization steps
    tier.step_limit = 2
    # Pass the 3 best candidates to be scored by the next tier. In this
    # example, the next tier is the "real" fitting objective final
    tier.accept = 3

    tier.fit_models = fit_models
    tier.fit_symbols = final.fit_symbols
    tiers = [tier]

    initial = final

    kv0 = mm.chemical_system_iter_keys(csys)

    # == 4. Optimize == #
    newcsys, (P0, P), (C0, C) = fits.ff_optimize(
        csys,
        gdb,
        psys,
        strat,
        co,
        initial,
        tiers,
        final
    )

    # == Done. Print out the parameter values
    print("Modified parameters:")
    kv = mm.chemical_system_iter_keys(newcsys)
    for k, v in kv.items():
        v0 = kv0.get(k)
        param_line = f"{str(k):20s} | New: {v:12.6g}"
        if v0 is not None:
            dv = v-v0
            if abs(dv) < 1e-7:
                continue
            line = param_line + f" Ref {v0:12.6g} Diff {dv:12.6g}"
        else:
            line = param_line
        print(line)

    # Show the objectives, before and after
    print("Initial objectives:")
    # P is the physical objective (positions, gradients), C is the chemical
    # objective (SMARTS complexity, number of SMARTS)
    X0 = P0 + C0
    X = P + C
    print(f"Total= {X0:15.8g} Physical {P0:15.8g} Chemical {C0:15.8g}")
    print("Final objectives:")
    print(f"Total= {X:15.8g} Physical {P:15.8g} Chemical {C:15.8g}")
    print("Differences:")
    print(
        f"Total= {100*(X-X0)/X0:14.2f}%",
        f"Physical {100*(P-P0)/P0:14.2f}%",
        f"Chemical {100*(C-C0)/C0:14.2f}%"
    )


xml = """<?xml version="1.0" encoding="utf-8"?>
<SMIRNOFF version="0.3" aromaticity_model="AROMATICITY_MDL">
    <Constraints version="0.3">
    </Constraints>
    <Bonds version="0.4" potential="harmonic" fractional_bondorder_method="AM1-Wiberg" fractional_bondorder_interpolation="linear">
        <Bond smirks="[#6X4:1]-[#6X4:2]" id="b1" length="1.527940216866 * angstrom" k="419.9869268191 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#6X4:1]-[#6X3:2]" id="b2" length="1.503434271105 * angstrom" k="484.1959214883 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#6X4:1]-[#6X3:2]=[#8X1+0]" id="b3" length="1.529478304416 * angstrom" k="418.6331368515 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#6X3:1]-[#6X3:2]" id="b4" length="1.466199291912 * angstrom" k="540.3345953498 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#6X3:1]:[#6X3:2]" id="b5" length="1.394445702699 * angstrom" k="765.1465671607 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#6X3:1]=[#6X3:2]" id="b6" length="1.382361687103 * angstrom" k="898.589948525 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#6:1]-[#7:2]" id="b7" length="1.46420197713 * angstrom" k="457.1029448115 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#6X3:1]-[#7X3:2]" id="b8" length="1.389681126838 * angstrom" k="640.6150893356 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#6X4:1]-[#7X3:2]-[#6X3]=[#8X1+0]" id="b9" length="1.469242986682 * angstrom" k="467.3752485468 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#6X3:1](=[#8X1+0])-[#7X3:2]" id="b10" length="1.388092539119 * angstrom" k="644.6314222627 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#6X3:1]-[#7X2:2]" id="b11" length="1.366329573172 * angstrom" k="566.4793948211 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#6X3:1]:[#7X2,#7X3+1:2]" id="b12" length="1.337191333766 * angstrom" k="760.4093054565 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#6X3:1]=[#7X2,#7X3+1:2]" id="b13" length="1.306529281865 * angstrom" k="1023.286029691 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#6X3:1](~!@[#7X3])(~!@[#7X3])~!@[#7X3:2]" id="b13a" length="1.304468222569 * angstrom" k="1171.510786135 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#6:1]-[#8:2]" id="b14" length="1.423822414975 * angstrom" k="545.2782783431 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#6X3:1]-[#8X1-1:2]" id="b15" length="1.278958196232 * angstrom" k="1090.071176574 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#6X4:1]-[#8X2H0:2]" id="b16" length="1.421832315661 * angstrom" k="434.3139352817 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#6X3:1]-[#8X2:2]" id="b17" length="1.357746519746 * angstrom" k="598.9859275918 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#6X3:1]-[#8X2H1:2]" id="b18" length="1.367997231102 * angstrom" k="673.9493155918 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#6X3a:1]-[#8X2H0:2]" id="b19" length="1.375666333304 * angstrom" k="650.5820092964 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#6X3:1](=[#8X1])-[#8X2H0:2]" id="b20" length="1.329462769246 * angstrom" k="584.2817678325 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#6:1]=[#8X1+0,#8X2+1:2]" id="b21" length="1.221668642702 * angstrom" k="1527.019744047 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#6X3:1](~[#8X1])~[#8X1:2]" id="b22" length="1.254210140463 * angstrom" k="1187.240374941 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#6X3:1]~[#8X2+1:2]~[#6X3]" id="b23" length="1.381088666112 * angstrom" k="603.5798890353 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#6X2:1]-[#6:2]" id="b24" length="1.441393771474 * angstrom" k="669.7030665096 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#6X2:1]-[#6X4:2]" id="b25" length="1.501586407595 * angstrom" k="600.4776530155 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#6X2:1]=[#6X3:2]" id="b26" length="1.317791710223 * angstrom" k="1338.556990597 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#6:1]#[#7:2]" id="b27" length="1.157453837528 * angstrom" k="2687.724097656 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#6X2:1]#[#6X2:2]" id="b28" length="1.225366047596 * angstrom" k="2349.404717881 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#6X2:1]-[#8X2:2]" id="b29" length="1.322622550558 * angstrom" k="922.9703949352 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#6X2:1]-[#7:2]" id="b30" length="1.338472802194 * angstrom" k="935.7833626951 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#6X2:1]=[#7:2]" id="b31" length="1.218519372373 * angstrom" k="1902.697248199 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#16:1]=[#6:2]" id="b32" length="1.667214675226 * angstrom" k="542.8835638291 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#6X2:1]=[#16:2]" id="b33" length="1.58859904289 * angstrom" k="864.7801541974 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#7:1]-[#7:2]" id="b34" length="1.419653358459 * angstrom" k="578.3171956538 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#7X3:1]-[#7X2:2]" id="b35" length="1.379823478248 * angstrom" k="620.3703294578 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#7X2:1]-[#7X2:2]" id="b36" length="1.320508819182 * angstrom" k="472.7986168038 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#7:1]:[#7:2]" id="b37" length="1.358867129801 * angstrom" k="661.890337193 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#7:1]=[#7:2]" id="b38" length="1.308551882841 * angstrom" k="1089.095154418 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#7+1:1]=[#7-1:2]" id="b39" length="1.145334803355 * angstrom" k="2440.219143191 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#7:1]#[#7:2]" id="b40" length="1.117035355131 * angstrom" k="3236.625411136 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#7:1]-[#8X2:2]" id="b41" length="1.352286461624 * angstrom" k="436.4925993782 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#7:1]~[#8X1:2]" id="b42" length="1.272967337826 * angstrom" k="1181.979770202 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#8X2:1]-[#8X2,#8X1-1:2]" id="b43" length="1.417654481737 * angstrom" k="425.3980361958 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#16:1]-[#6:2]" id="b44" length="1.807204863403 * angstrom" k="474.0210361996 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#16:1]-[#1:2]" id="b45" length="1.343434299302 * angstrom" k="589.5574217095 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#16:1]-[#16:2]" id="b46" length="2.101256208464 * angstrom" k="273.3607326238 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#16:1]-[#9:2]" id="b47" length="1.6 * angstrom" k="750.0 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#16:1]-[#17:2]" id="b48" length="2.186801428667 * angstrom" k="176.8242039027 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#16:1]-[#35:2]" id="b49" length="2.329554946939 * angstrom" k="162.4657673725 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#16:1]-[#53:2]" id="b50" length="2.6 * angstrom" k="150.0 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#16X2,#16X1-1,#16X3+1:1]-[#6X4:2]" id="b51" length="1.802285070249 * angstrom" k="280.9754267567 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#16X2,#16X1-1,#16X3+1:1]-[#6X3:2]" id="b52" length="1.762285642167 * angstrom" k="365.1121821496 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#16X2,#16X1-1:1]-[#7:2]" id="b53" length="1.666974333764 * angstrom" k="194.9936941661 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#16X2:1]-[#8X2:2]" id="b54" length="1.668797245615 * angstrom" k="389.1107850666 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#16X2:1]=[#8X1,#7X2:2]" id="b55" length="1.518365940501 * angstrom" k="991.9416637116 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#16X4,#16X3!+1:1]-[#6:2]" id="b56" length="1.83163935697 * angstrom" k="332.7967625232 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#16X4,#16X3:1]~[#7:2]" id="b57" length="1.796264554872 * angstrom" k="335.2844938401 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#16X4,#16X3:1]~[#7X2:2]" id="b57a" length="1.739290000881 * angstrom" k="334.608811796 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#16X4,#16X3:1]-[#8X2:2]" id="b58" length="1.84420913088 * angstrom" k="290.4876917748 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#16X4,#16X3:1]~[#8X1:2]" id="b59" length="1.48043501819 * angstrom" k="1140.451821084 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#15:1]-[#1:2]" id="b60" length="1.411705067936 * angstrom" k="499.5822710564 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#15:1]~[#6:2]" id="b61" length="1.846557405399 * angstrom" k="356.0195037055 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#15:1]-[#7:2]" id="b62" length="1.6615956125588325 * angstrom" k="543.2032317304449 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#15:1]=[#7:2]" id="b63" length="1.601317589549 * angstrom" k="733.874030833 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#15:1]~[#8X2:2]" id="b64" length="1.65315684971 * angstrom" k="503.9075412178 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#15:1]~[#8X1:2]" id="b65" length="1.50900232257 * angstrom" k="1310.25019775 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#16:1]-[#15:2]" id="b66" length="2.109917427425 * angstrom" k="261.9537532314 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#15:1]=[#16X1:2]" id="b67" length="1.954856408044 * angstrom" k="447.2504231689 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#6:1]-[#9:2]" id="b68" length="1.351036117403 * angstrom" k="710.1945186755 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#6X4:1]-[#9:2]" id="b69" length="1.370653919259 * angstrom" k="535.7033772882 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#6:1]-[#17:2]" id="b70" length="1.722215272811 * angstrom" k="368.4266150848 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#6X4:1]-[#17:2]" id="b71" length="1.785584712269 * angstrom" k="243.9998472975 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#6:1]-[#35:2]" id="b72" length="1.918619202782 * angstrom" k="307.3240888512 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#6X4:1]-[#35:2]" id="b73" length="1.956125723081 * angstrom" k="208.6532205069 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#6:1]-[#53:2]" id="b74" length="2.198076696503 * angstrom" k="72.93341107584 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#6X4:1]-[#53:2]" id="b75" length="2.166 * angstrom" k="296.0 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#7:1]-[#9:2]" id="b76" length="1.451207387384 * angstrom" k="454.200954174 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#7:1]-[#17:2]" id="b77" length="1.790010591282 * angstrom" k="294.4949955218 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#7:1]-[#35:2]" id="b78" length="1.874625497662 * angstrom" k="322.5593272257 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#7:1]-[#53:2]" id="b79" length="2.1 * angstrom" k="160.0 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#15:1]-[#9:2]" id="b80" length="1.64 * angstrom" k="880.0 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#15:1]-[#17:2]" id="b81" length="2.058765007678 * angstrom" k="283.910296871 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#15:1]-[#35:2]" id="b82" length="2.272769579468 * angstrom" k="232.7738856239 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#15:1]-[#53:2]" id="b83" length="2.6 * angstrom" k="140.0 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#6X4:1]-[#1:2]" id="b84" length="1.090139506109 * angstrom" k="719.6424928981 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#6X3:1]-[#1:2]" id="b85" length="1.081823673944 * angstrom" k="775.3853383846 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#6X2:1]-[#1:2]" id="b86" length="1.084500073436 * angstrom" k="932.1739669865 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#7:1]-[#1:2]" id="b87" length="1.022553377106 * angstrom" k="964.6719203843 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
        <Bond smirks="[#8:1]-[#1:2]" id="b88" length="0.981124525388 * angstrom" k="1069.809209734 * angstrom**-2 * mole**-1 * kilocalorie"></Bond>
    </Bonds>
    <Angles version="0.3" potential="harmonic">
        <Angle smirks="[*:1]~[#6X4:2]-[*:3]" angle="110.0631999136 * degree" k="121.1883270155 * mole**-1 * radian**-2 * kilocalorie" id="a1"></Angle>
        <Angle smirks="[#1:1]-[#6X4:2]-[#1:3]" angle="108.5839257083 * degree" k="75.08254435747 * mole**-1 * radian**-2 * kilocalorie" id="a2"></Angle>
        <Angle smirks="[*;r3:1]1~;@[*;r3:2]~;@[*;r3:3]1" angle="60.85328214995 * degree" k="122.716552253 * mole**-1 * radian**-2 * kilocalorie" id="a3"></Angle>
        <Angle smirks="[*;r3:1]~;@[*;r3:2]~;!@[*:3]" angle="116.9378850233 * degree" k="64.11672720227 * mole**-1 * radian**-2 * kilocalorie" id="a4"></Angle>
        <Angle smirks="[*:1]~;!@[*;r3:2]~;!@[*:3]" angle="117.2642149656 * degree" k="104.4915917827 * mole**-1 * radian**-2 * kilocalorie" id="a5"></Angle>
        <Angle smirks="[#1:1]-[*;r3:2]~;!@[*:3]" angle="113.9737197428 * degree" k="35.92183082353 * mole**-1 * radian**-2 * kilocalorie" id="a6"></Angle>
        <Angle smirks="[#6r4:1]-;@[#6r4:2]-;@[#6r4:3]" angle="95.54319985822 * degree" k="123.3865012309 * mole**-1 * radian**-2 * kilocalorie" id="a7"></Angle>
        <Angle smirks="[!#1:1]-[#6r4:2]-;!@[!#1:3]" angle="119.6176402745 * degree" k="116.3733322438 * mole**-1 * radian**-2 * kilocalorie" id="a8"></Angle>
        <Angle smirks="[!#1:1]-[#6r4:2]-;!@[#1:3]" angle="113.5521836954 * degree" k="155.3758694614 * mole**-1 * radian**-2 * kilocalorie" id="a9"></Angle>
        <Angle smirks="[*:1]~[#6X3:2]~[*:3]" angle="119.8314000445 * degree" k="147.0414413301 * mole**-1 * radian**-2 * kilocalorie" id="a10"></Angle>
        <Angle smirks="[#1:1]-[#6X3:2]~[*:3]" angle="119.6660147945 * degree" k="61.76277021281 * mole**-1 * radian**-2 * kilocalorie" id="a11"></Angle>
        <Angle smirks="[#1:1]-[#6X3:2]-[#1:3]" angle="115.9051715467 * degree" k="48.36265688271 * mole**-1 * radian**-2 * kilocalorie" id="a12"></Angle>
        <Angle smirks="[*;r6:1]~;@[*;r5:2]~;@[*;r5;x2:3]" angle="123.3883854468 * degree" k="94.63724536934 * mole**-1 * radian**-2 * kilocalorie" id="a13"></Angle>
        <Angle smirks="[*:1]~;!@[*;X3;r5:2]~;@[*;r5:3]" angle="124.7638724138 * degree" k="83.72517627035 * mole**-1 * radian**-2 * kilocalorie" id="a14"></Angle>
        <Angle smirks="[#8X1:1]~[#6X3:2]~[#8:3]" angle="123.884602033 * degree" k="157.683696058 * mole**-1 * radian**-2 * kilocalorie" id="a15"></Angle>
        <Angle smirks="[*:1]~[#6X2:2]~[*:3]" angle="178.03216488466285 * degree" k="90.9419905012 * mole**-1 * radian**-2 * kilocalorie" id="a16"></Angle>
        <Angle smirks="[*:1]~[#7X2:2]~[*:3]" angle="176.02345454674733 * degree" k="92.84676041839 * mole**-1 * radian**-2 * kilocalorie" id="a17"></Angle>
        <Angle smirks="[*:1]~[#7X4,#7X3,#7X2-1:2]~[*:3]" angle="113.0535542176 * degree" k="229.7366557677 * mole**-1 * radian**-2 * kilocalorie" id="a18"></Angle>
        <Angle smirks="[*:1]@-[r!r6;#7X4,#7X3,#7X2-1:2]@-[*:3]" angle="105.8401571906 * degree" k="265.3585554223 * mole**-1 * radian**-2 * kilocalorie" id="a18a"></Angle>
        <Angle smirks="[#1:1]-[#7X4,#7X3,#7X2-1:2]-[*:3]" angle="109.8280614024 * degree" k="93.85648326614 * mole**-1 * radian**-2 * kilocalorie" id="a19"></Angle>
        <Angle smirks="[*:1]~[#7X3$(*~[#6X3,#6X2,#7X2+0]):2]~[*:3]" angle="119.1748379248 * degree" k="151.142556131 * mole**-1 * radian**-2 * kilocalorie" id="a20"></Angle>
        <Angle smirks="[#1:1]-[#7X3$(*~[#6X3,#6X2,#7X2+0]):2]-[*:3]" angle="117.5760620116 * degree" k="71.15425408676 * mole**-1 * radian**-2 * kilocalorie" id="a21"></Angle>
        <Angle smirks="[*:1]~[#7X2+0:2]~[*:3]" angle="118.7711698022 * degree" k="272.4286544662 * mole**-1 * radian**-2 * kilocalorie" id="a22"></Angle>
        <Angle smirks="[*:1]~[#7X2+0r5:2]~[*:3]" angle="107.4649958639 * degree" k="284.6150923095 * mole**-1 * radian**-2 * kilocalorie" id="a22a"></Angle>
        <Angle smirks="[*:1]~[#7X2+0:2]~[#6X2:3](~[#16X1])" angle="145.0942288799 * degree" k="150.340273506 * mole**-1 * radian**-2 * kilocalorie" id="a23"></Angle>
        <Angle smirks="[#1:1]-[#7X2+0:2]~[*:3]" angle="115.552080361 * degree" k="214.9469380689 * mole**-1 * radian**-2 * kilocalorie" id="a24"></Angle>
        <Angle smirks="[#6,#7,#8:1]-[#7X3:2](~[#8X1])~[#8X1:3]" angle="121.0803418862 * degree" k="147.381217677 * mole**-1 * radian**-2 * kilocalorie" id="a25"></Angle>
        <Angle smirks="[#8X1:1]~[#7X3:2]~[#8X1:3]" angle="124.9682447718 * degree" k="136.5596518574 * mole**-1 * radian**-2 * kilocalorie" id="a26"></Angle>
        <Angle smirks="[*:1]~[#7X2:2]~[#7X1:3]" angle="175.86536907731292 * degree" k="101.8769252507 * mole**-1 * radian**-2 * kilocalorie" id="a27"></Angle>
        <Angle smirks="[*:1]-[#8:2]-[*:3]" angle="111.9874516071 * degree" k="237.851218935 * mole**-1 * radian**-2 * kilocalorie" id="a28"></Angle>
        <Angle smirks="[#6X3,#7:1]~;@[#8;r:2]~;@[#6X3,#7:3]" angle="108.1782929371 * degree" k="329.0368535669 * mole**-1 * radian**-2 * kilocalorie" id="a29"></Angle>
        <Angle smirks="[*:1]-[#8X2+1:2]=[*:3]" angle="125.1570722794 * degree" k="308.4405595435 * mole**-1 * radian**-2 * kilocalorie" id="a30"></Angle>
        <Angle smirks="[*:1]~[#16X4:2]~[*:3]" angle="117.3713508414 * degree" k="197.5762430878 * mole**-1 * radian**-2 * kilocalorie" id="a31"></Angle>
        <Angle smirks="[*:1]-[#16X4,#16X3+0:2]~[*:3]" angle="106.8069820626 * degree" k="134.3906472803 * mole**-1 * radian**-2 * kilocalorie" id="a32"></Angle>
        <Angle smirks="[*:1]~[#16X3$(*~[#8X1,#7X2]):2]~[*:3]" angle="104.5813282082 * degree" k="231.9047915019 * mole**-1 * radian**-2 * kilocalorie" id="a33"></Angle>
        <Angle smirks="[*:1]~[#16X2,#16X3+1:2]~[*:3]" angle="101.2115918366 * degree" k="190.2357159589 * mole**-1 * radian**-2 * kilocalorie" id="a34"></Angle>
        <Angle smirks="[*:1]=[#16X2:2]=[*:3]" angle="180.0 * degree" k="140.0 * mole**-1 * radian**-2 * kilocalorie" id="a35"></Angle>
        <Angle smirks="[*:1]=[#16X2:2]=[#8:3]" angle="112.654344981 * degree" k="260.0878085059 * mole**-1 * radian**-2 * kilocalorie" id="a36"></Angle>
        <Angle smirks="[#6X3:1]-[#16X2:2]-[#6X3:3]" angle="92.71824501964 * degree" k="219.4156240153 * mole**-1 * radian**-2 * kilocalorie" id="a37"></Angle>
        <Angle smirks="[#6X3:1]-[#16X2:2]-[#6X4:3]" angle="99.19186516382 * degree" k="283.1221563133 * mole**-1 * radian**-2 * kilocalorie" id="a38"></Angle>
        <Angle smirks="[#6X3:1]-[#16X2:2]-[#1:3]" angle="94.99788292909 * degree" k="171.0404573417 * mole**-1 * radian**-2 * kilocalorie" id="a39"></Angle>
        <Angle smirks="[*:1]~[#15:2]~[*:3]" angle="108.3772583309 * degree" k="136.7523220166 * mole**-1 * radian**-2 * kilocalorie" id="a40"></Angle>
    </Angles>
    <ProperTorsions version="0.4" potential="k*(1+cos(periodicity*theta-phase))" default_idivf="auto" fractional_bondorder_method="AM1-Wiberg" fractional_bondorder_interpolation="linear">
        <Proper smirks="[*:1]-[#6X4:2]-[#6X4:3]-[*:4]" periodicity1="3" phase1="0.0 * degree" id="t1" k1="0.1526959283148 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[#6X4:1]-[#6X4:2]-[#6X4:3]-[#6X4:4]" periodicity1="3" periodicity2="2" periodicity3="1" phase1="0.0 * degree" phase2="180.0 * degree" phase3="180.0 * degree" id="t2" k1="0.42948937236 * mole**-1 * kilocalorie" k2="0.2543919562345 * mole**-1 * kilocalorie" k3="0.8736160241398 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0" idivf3="1.0"></Proper>
        <Proper smirks="[#1:1]-[#6X4:2]-[#6X4:3]-[#1:4]" periodicity1="3" phase1="0.0 * degree" id="t3" k1="0.2516073078789 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[#1:1]-[#6X4:2]-[#6X4:3]-[#6X4:4]" periodicity1="3" phase1="0.0 * degree" id="t4" k1="0.08586880062944 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[#8X2:1]-[#6X4:2]-[#6X4:3]-[#8X2:4]" periodicity1="3" periodicity2="2" phase1="0.0 * degree" phase2="0.0 * degree" id="t5" k1="-0.07074403224063 * mole**-1 * kilocalorie" k2="0.3931231741139 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0"></Proper>
        <Proper smirks="[#9:1]-[#6X4:2]-[#6X4:3]-[#9:4]" periodicity1="3" periodicity2="1" phase1="0.0 * degree" phase2="180.0 * degree" id="t6" k1="0.07374657685912 * mole**-1 * kilocalorie" k2="-0.1972790277243 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0"></Proper>
        <Proper smirks="[#17:1]-[#6X4:2]-[#6X4:3]-[#17:4]" periodicity1="3" periodicity2="1" phase1="0.0 * degree" phase2="180.0 * degree" id="t7" k1="0.6406243801433 * mole**-1 * kilocalorie" k2="-1.405165265086 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0"></Proper>
        <Proper smirks="[#35:1]-[#6X4:2]-[#6X4:3]-[#35:4]" periodicity1="3" periodicity2="1" phase1="0.0 * degree" phase2="180.0 * degree" id="t8" k1="1.077457566744 * mole**-1 * kilocalorie" k2="-0.1136715754252 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0"></Proper>
        <Proper smirks="[#1:1]-[#6X4:2]-[#6X4:3]-[#8X2:4]" periodicity1="3" periodicity2="1" phase1="0.0 * degree" phase2="0.0 * degree" id="t9" k1="0.1006464828354 * mole**-1 * kilocalorie" k2="0.482791305955 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0"></Proper>
        <Proper smirks="[#1:1]-[#6X4:2]-[#6X4:3]-[#9:4]" periodicity1="3" periodicity2="1" phase1="0.0 * degree" phase2="0.0 * degree" id="t10" k1="0.1074498529241 * mole**-1 * kilocalorie" k2="0.4261849649125 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0"></Proper>
        <Proper smirks="[#1:1]-[#6X4:2]-[#6X4:3]-[#17:4]" periodicity1="3" periodicity2="1" phase1="0.0 * degree" phase2="0.0 * degree" id="t11" k1="0.2194553771389 * mole**-1 * kilocalorie" k2="0.6937444435835 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0"></Proper>
        <Proper smirks="[#1:1]-[#6X4:2]-[#6X4:3]-[#35:4]" periodicity1="3" periodicity2="1" phase1="0.0 * degree" phase2="0.0 * degree" id="t12" k1="0.1338552866344 * mole**-1 * kilocalorie" k2="0.6076851605113 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0"></Proper>
        <Proper smirks="[*:1]-[#6X4:2]-[#6X4;r3:3]-[*:4]" periodicity1="1" phase1="0.0 * degree" id="t13" k1="1.758154369737 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]-[#6X4:2]-[#6X4;r3:3]-[#6X4;r3:4]" periodicity1="3" phase1="0.0 * degree" id="t14" k1="0.3832336907808 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]-[#6X4;r3:2]-@[#6X4;r3:3]-[*:4]" periodicity1="2" phase1="0.0 * degree" id="t15" k1="-2.743657257903 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[#6X4;r3:1]-[#6X4;r3:2]-[#6X4;r3:3]-[*:4]" periodicity1="2" periodicity2="1" phase1="0.0 * degree" phase2="0.0 * degree" id="t16" k1="-0.6703166759336 * mole**-1 * kilocalorie" k2="4.597466489339 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0"></Proper>
        <Proper smirks="[*:1]~[#6X3:2]-[#6X4:3]-[*:4]" periodicity1="3" phase1="0.0 * degree" id="t17" k1="0.1812602534451 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]-[#6X4:2]-[#6X3:3]=[*:4]" periodicity1="2" phase1="0.0 * degree" id="t18" k1="-0.427959867982 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]-[#6X4:2]-[#6X3:3](~[#8X1])~[#8X1:4]" periodicity1="2" phase1="0.0 * degree" id="t18a" k1="-0.2045307565273 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]-[#6X4:2]-[#6X3:3](~!@[#7X3])~!@[#7X3:4]" periodicity1="2" phase1="0.0 * degree" id="t18b" k1="-0.1892404337724 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[#1:1]-[#6X4:2]-[#6X3:3]=[#8X1:4]" periodicity1="1" periodicity2="2" periodicity3="3" phase1="0.0 * degree" phase2="0.0 * degree" phase3="180.0 * degree" id="t19" k1="0.9162969507922 * mole**-1 * kilocalorie" k2="0.208078889572 * mole**-1 * kilocalorie" k3="-0.1737796012683 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0" idivf3="1.0"></Proper>
        <Proper smirks="[#1:1]-[#6X4:2]-[#6X3:3](~[#8X1])~[#8X1:4]" periodicity1="1" periodicity2="2" periodicity3="3" phase1="0.0 * degree" phase2="0.0 * degree" phase3="180.0 * degree" id="t19a" k1="-0.05752542044084 * mole**-1 * kilocalorie" k2="0.3423737582396 * mole**-1 * kilocalorie" k3="0.2544090180879 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0" idivf3="1.0"></Proper>
        <Proper smirks="[#1:1]-[#6X4:2]-[#6X3:3]=[#6X3:4]" periodicity1="3" periodicity2="1" phase1="180.0 * degree" phase2="0.0 * degree" id="t20" k1="0.2699187392383 * mole**-1 * kilocalorie" k2="0.1936581772637 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0"></Proper>
        <Proper smirks="[#6X3:1]-[#6X4:2]-[#6X3:3]=[#6X3:4]" periodicity1="3" periodicity2="2" phase1="0.0 * degree" phase2="180.0 * degree" id="t21" k1="-0.1479457423917 * mole**-1 * kilocalorie" k2="0.5642954954317 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0"></Proper>
        <Proper smirks="[#7X3:1]-[#6X4:2]-[#6X3:3]-[#7X3:4]" periodicity1="1" periodicity2="2" phase1="180.0 * degree" phase2="180.0 * degree" id="t22" k1="-0.4910075247491 * mole**-1 * kilocalorie" k2="0.5641713237747 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0"></Proper>
        <Proper smirks="[#6X4:1]-[#6X4:2]-[#6X3:3]-[#7X3:4]" periodicity1="4" periodicity2="2" phase1="0.0 * degree" phase2="0.0 * degree" id="t23" k1="-0.2849592935905 * mole**-1 * kilocalorie" k2="0.2705828028812 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0"></Proper>
        <Proper smirks="[#16X2,#16X1-1,#16X3+1:1]-[#6X3:2]-[#6X4:3]-[#1:4]" periodicity1="2" periodicity2="1" phase1="0.0 * degree" phase2="180.0 * degree" id="t24" k1="-0.6027249766107 * mole**-1 * kilocalorie" k2="-0.2875027046256 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0"></Proper>
        <Proper smirks="[#16X2,#16X1-1,#16X3+1:1]-[#6X3:2]-[#6X4:3]-[#7X4,#7X3:4]" periodicity1="4" periodicity2="3" periodicity3="2" periodicity4="2" periodicity5="1" phase1="0.0 * degree" phase2="0.0 * degree" phase3="0.0 * degree" phase4="270.0 * degree" phase5="90.0 * degree" id="t25" k1="-0.2577324477985 * mole**-1 * kilocalorie" k2="-0.1781695657288 * mole**-1 * kilocalorie" k3="-0.8015507420074 * mole**-1 * kilocalorie" k4="0.07125763939923 * mole**-1 * kilocalorie" k5="0.08110677910138 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0" idivf3="1.0" idivf4="1.0" idivf5="1.0"></Proper>
        <Proper smirks="[#16X2,#16X1-1,#16X3+1:1]-[#6X3:2]-[#6X4:3]-[#7X3$(*-[#6X3,#6X2]):4]" periodicity1="4" periodicity2="3" periodicity3="2" periodicity4="2" periodicity5="1" periodicity6="1" phase1="270.0 * degree" phase2="0.0 * degree" phase3="180.0 * degree" phase4="270.0 * degree" phase5="270.0 * degree" phase6="0.0 * degree" id="t26" k1="0.1821840310834 * mole**-1 * kilocalorie" k2="-0.02943549724318 * mole**-1 * kilocalorie" k3="0.7822073629734 * mole**-1 * kilocalorie" k4="-0.1154354573733 * mole**-1 * kilocalorie" k5="-0.09037591883258 * mole**-1 * kilocalorie" k6="-0.5152151612155 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0" idivf3="1.0" idivf4="1.0" idivf5="1.0" idivf6="1.0"></Proper>
        <Proper smirks="[*:1]-[#6X4;r3:2]-[#6X3:3]~[*:4]" periodicity1="1" phase1="0.0 * degree" id="t27" k1="0.4476220345854 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[#6X4:1]-[#6X4;r3:2]-[#6X3:3]~[#6X3:4]" periodicity1="4" periodicity2="2" phase1="180.0 * degree" phase2="180.0 * degree" id="t28" k1="0.4906962395955 * mole**-1 * kilocalorie" k2="0.1972392762662 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0"></Proper>
        <Proper smirks="[#1:1]-[#6X4;r3:2]-[#6X3:3]~[#6X3:4]" periodicity1="4" periodicity2="3" periodicity3="2" phase1="180.0 * degree" phase2="0.0 * degree" phase3="180.0 * degree" id="t29" k1="0.03365480695677 * mole**-1 * kilocalorie" k2="0.3315337048378 * mole**-1 * kilocalorie" k3="-0.1813801692186 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0" idivf3="1.0"></Proper>
        <Proper smirks="[#6X3:1]-[#6X4;r3:2]-[#6X3:3]-[#7X3:4]" periodicity1="2" periodicity2="1" periodicity3="3" phase1="0.0 * degree" phase2="180.0 * degree" phase3="180.0 * degree" id="t30" k1="-0.4094047131311 * mole**-1 * kilocalorie" k2="0.7214198085497 * mole**-1 * kilocalorie" k3="-0.4213069561783 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0" idivf3="1.0"></Proper>
        <Proper smirks="[#6X3:1]-[#6X4;r3:2]-[#6X3:3]=[#8X1:4]" periodicity1="2" periodicity2="1" periodicity3="3" phase1="180.0 * degree" phase2="180.0 * degree" phase3="180.0 * degree" id="t31" k1="1.189964268076 * mole**-1 * kilocalorie" k2="-0.621218830807 * mole**-1 * kilocalorie" k3="0.06495844961585 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0" idivf3="1.0"></Proper>
        <Proper smirks="[#6X3:1]-[#6X4;r3:2]-[#6X3:3](~[#8X1])~[#8X1:4]" periodicity1="2" periodicity2="1" periodicity3="3" phase1="180.0 * degree" phase2="180.0 * degree" phase3="180.0 * degree" id="t31a" k1="1.761355676865 * mole**-1 * kilocalorie" k2="-0.2233803656273 * mole**-1 * kilocalorie" k3="-0.08184607083998 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0" idivf3="1.0"></Proper>
        <Proper smirks="[#6X3:1]-[#6X4;r3:2]-[#6X3:3]~[#6X3:4]" periodicity1="2" phase1="180.0 * degree" id="t32" k1="-0.4168025326006 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[#7X3:1]-[#6X4;r3:2]-[#6X3:3]~[#6X3:4]" periodicity1="4" periodicity2="2" phase1="180.0 * degree" phase2="180.0 * degree" id="t33" k1="0.06747020256501 * mole**-1 * kilocalorie" k2="-0.3648818643986 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0"></Proper>
        <Proper smirks="[#6X4;r3:1]-;@[#6X4;r3:2]-[#6X3:3]~[#6X3:4]" periodicity1="4" periodicity2="3" periodicity3="2" phase1="180.0 * degree" phase2="0.0 * degree" phase3="180.0 * degree" id="t34" k1="0.2471586385669 * mole**-1 * kilocalorie" k2="-2.709272212403 * mole**-1 * kilocalorie" k3="2.355440994331 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0" idivf3="1.0"></Proper>
        <Proper smirks="[#6X4;r3:1]-;@[#6X4;r3:2]-[#6X3;r6:3]:[#6X3;r6:4]" periodicity1="4" periodicity2="2" phase1="180.0 * degree" phase2="180.0 * degree" id="t35" k1="0.02771185535345 * mole**-1 * kilocalorie" k2="1.453560077229 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0"></Proper>
        <Proper smirks="[#6X4;r3:1]-;@[#6X4;r3:2]-[#6X3;r5:3]-;@[#6X3;r5:4]" periodicity1="4" periodicity2="3" periodicity3="2" phase1="180.0 * degree" phase2="0.0 * degree" phase3="180.0 * degree" id="t36" k1="-0.05206457676159 * mole**-1 * kilocalorie" k2="0.1335413865937 * mole**-1 * kilocalorie" k3="2.332577530677 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0" idivf3="1.0"></Proper>
        <Proper smirks="[#6X4;r3:1]-;@[#6X4;r3:2]-[#6X3;r5:3]=;@[#6X3;r5:4]" periodicity1="1" phase1="180.0 * degree" id="t37" k1="-0.2801379561603 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[#6X4;r3:1]-;@[#6X4;r3:2]-[#6X3:3]-[#6X4:4]" periodicity1="1" phase1="0.0 * degree" id="t38" k1="0.4155766290429 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[#6X4;r3:1]-;@[#6X4;r3:2]-[#6X3;r6:3]:[#7X2;r6:4]" periodicity1="2" periodicity2="1" periodicity3="3" phase1="180.0 * degree" phase2="180.0 * degree" phase3="0.0 * degree" id="t39" k1="2.095796028375 * mole**-1 * kilocalorie" k2="-0.4654644265313 * mole**-1 * kilocalorie" k3="1.312846277337 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0" idivf3="1.0"></Proper>
        <Proper smirks="[#6X4;r3:1]-;@[#6X4;r3:2]-[#6X3:3]=[#7X2:4]" periodicity1="3" periodicity2="2" periodicity3="1" phase1="0.0 * degree" phase2="180.0 * degree" phase3="180.0 * degree" id="t40" k1="-0.8888508403012 * mole**-1 * kilocalorie" k2="1.347547544013 * mole**-1 * kilocalorie" k3="-0.1268083299631 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0" idivf3="1.0"></Proper>
        <Proper smirks="[#6X4;r3:1]-;@[#6X4;r3:2]-[#6X3:3]-[#8X2:4]" periodicity1="4" periodicity2="2" phase1="180.0 * degree" phase2="180.0 * degree" id="t41" k1="-0.1000623775332 * mole**-1 * kilocalorie" k2="2.45413408332 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0"></Proper>
        <Proper smirks="[#6X4;r3:1]-;@[#6X4;r3:2]-[#6X3:3]=[#8X1:4]" periodicity1="2" phase1="320.0 * degree" id="t42" k1="-0.9479154065382 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[#6X4;r3:1]-;@[#6X4;r3:2]-[#6X3:3](~[#8X1])~[#8X1:4]" periodicity1="2" phase1="320.0 * degree" id="t42a" k1="-0.4826638180162 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]~[#6X3:2]-[#6X3:3]~[*:4]" periodicity1="2" phase1="180.0 * degree" id="t43" k1="1.229562662833 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]~[#6X3:2]:[#6X3:3]~[*:4]" periodicity1="2" phase1="180.0 * degree" id="t44" k1="3.262660352984 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]-,:[#6X3:2]=[#6X3:3]-,:[*:4]" periodicity1="2" phase1="180.0 * degree" id="t45" k1="4.654738058203 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[#6X4:1]-[#6X3:2]=[#6X3:3]-[#6X4:4]" periodicity1="2" periodicity2="1" phase1="180.0 * degree" phase2="180.0 * degree" id="t46" k1="3.154324713915 * mole**-1 * kilocalorie" k2="-0.4171138742327 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0"></Proper>
        <Proper smirks="[*:1]~[#6X3:2]-[#6X3$(*=[#8,#16,#7]):3]~[*:4]" periodicity1="2" phase1="180.0 * degree" id="t47" k1="1.05588634537 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[#6X3:1]=[#6X3:2]-[#6X3:3]=[#8X1:4]" periodicity1="2" periodicity2="3" phase1="180.0 * degree" phase2="0.0 * degree" id="t48" k1="1.052002608629 * mole**-1 * kilocalorie" k2="0.6226434850844 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0"></Proper>
        <Proper smirks="[#6X3:1]=[#6X3:2]-[#6X3:3](~[#8X1])~[#8X1:4]" periodicity1="2" periodicity2="3" phase1="180.0 * degree" phase2="0.0 * degree" id="t48a" k1="-0.02403160784339 * mole**-1 * kilocalorie" k2="-1.083518405649 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0"></Proper>
        <Proper smirks="[*:1]~[#7a:2]:[#6a:3]~[*:4]" periodicity1="2" phase1="180.0 * degree" id="t49" k1="4.305064155732 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]-[#6X4:2]-[#7X4:3]-[*:4]" periodicity1="3" phase1="0.0 * degree" id="t50" k1="0.1184428518662 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]~[#6X4:2]-[#7X3:3]~[*:4]" periodicity1="3" phase1="0.0 * degree" id="t51" k1="0.3059380486721 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]-[#6X4:2]-[#7X3:3]-[#7X2:4]=[#6]" periodicity1="3" periodicity2="2" phase1="0.0 * degree" phase2="180.0 * degree" id="t52" k1="0.6310755461662 * mole**-1 * kilocalorie" k2="-0.2026027068942 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0"></Proper>
        <Proper smirks="[#1:1]-[#6X4:2]-[#7X3:3]-[#7X2:4]=[#6]" periodicity1="3" periodicity2="2" phase1="0.0 * degree" phase2="180.0 * degree" id="t53" k1="0.3699587466204 * mole**-1 * kilocalorie" k2="-0.1698598510357 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0"></Proper>
        <Proper smirks="[*:1]-[#6X4:2]-[#7X3:3]-[#7X2:4]=[#7X2,#8X1]" periodicity1="3" periodicity2="2" phase1="0.0 * degree" phase2="180.0 * degree" id="t54" k1="-0.4941263934832 * mole**-1 * kilocalorie" k2="3.716860734358 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0"></Proper>
        <Proper smirks="[#1:1]-[#6X4:2]-[#7X3:3]-[#7X2:4]=[#7X2,#8X1]" periodicity1="3" periodicity2="2" phase1="0.0 * degree" phase2="180.0 * degree" id="t55" k1="0.2431981267078 * mole**-1 * kilocalorie" k2="4.606848645298 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0"></Proper>
        <Proper smirks="[*:1]-[#6X4:2]-[#7X3$(*@1-[*]=,:[*][*]=,:[*]@1):3]-[*:4]" periodicity1="2" phase1="180.0 * degree" id="t56" k1="0.5332542282101 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[#1:1]-[#6X4:2]-[#7X3$(*@1-[*]=,:[*][*]=,:[*]@1):3]-[*:4]" periodicity1="2" phase1="180.0 * degree" id="t57" k1="0.8515019680046 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[#6X4:1]-[#6X4:2]-[#7X4,#7X3:3]-[#6X4:4]" periodicity1="3" periodicity2="2" phase1="0.0 * degree" phase2="180.0 * degree" id="t58" k1="0.04864727242766 * mole**-1 * kilocalorie" k2="0.2157911511699 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0"></Proper>
        <Proper smirks="[#1:1]-[#7X4,#7X3:2]-[#6X4;r3:3]-[*:4]" periodicity1="1" phase1="0.0 * degree" id="t59" k1="-1.355296923315 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[#1:1]-[#7X4,#7X3:2]-[#6X4;r3:3]-[#6X4;r3:4]" periodicity1="1" phase1="0.0 * degree" id="t60" k1="-0.105740042792 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[!#1:1]-[#7X4,#7X3:2]-[#6X4;r3:3]-[*:4]" periodicity1="3" phase1="0.0 * degree" id="t61" k1="0.2510036544347 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[!#1:1]-[#7X4,#7X3:2]-[#6X4;r3:3]-[#6X4;r3:4]" periodicity1="3" phase1="0.0 * degree" id="t62" k1="1.256849873507 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]-[#7X4:2]-[#6X3:3]~[*:4]" periodicity1="1" phase1="0.0 * degree" id="t63" k1="-0.003553710469134 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]-[#6X4:2]-[#7X3$(*~[#6X3,#6X2]):3]~[*:4]" periodicity1="2" periodicity2="3" phase1="0.0 * degree" phase2="0.0 * degree" id="t64" k1="0.3211560582805 * mole**-1 * kilocalorie" k2="0.2531822532171 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0"></Proper>
        <Proper smirks="[*:1]-[#6X4:2]-[#7X3$(*~[#8X1]):3]~[#8X1:4]" periodicity1="3" phase1="0.0 * degree" id="t65" k1="0.06138106438945 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[#6X3:1]-[#7X3:2]-[#6X4:3]-[#6X3:4]" periodicity1="2" periodicity2="1" phase1="180.0 * degree" phase2="0.0 * degree" id="t66" k1="-0.5610892792494 * mole**-1 * kilocalorie" k2="-0.5658765492977 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0"></Proper>
        <Proper smirks="[#6X4:1]-[#6X4:2]-[#7X3:3]-[#6X3:4]=[#8,#16,#7]" periodicity1="4" periodicity2="3" periodicity3="2" periodicity4="1" phase1="180.0 * degree" phase2="180.0 * degree" phase3="0.0 * degree" phase4="0.0 * degree" id="t67" k1="-0.1002944549968 * mole**-1 * kilocalorie" k2="-0.2346655100883 * mole**-1 * kilocalorie" k3="0.857667309467 * mole**-1 * kilocalorie" k4="-0.1020077532452 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0" idivf3="1.0" idivf4="1.0"></Proper>
        <Proper smirks="[#8X2H0:1]-[#6X4:2]-[#7X3:3]-[#6X3:4]" periodicity1="2" periodicity2="1" phase1="0.0 * degree" phase2="0.0 * degree" id="t68" k1="1.890066270967 * mole**-1 * kilocalorie" k2="-0.6501381532324 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0"></Proper>
        <Proper smirks="[#6X3:1]-[#7X3:2]-[#6X4;r3:3]-[#6X4;r3:4]" periodicity1="3" periodicity2="2" periodicity3="1" phase1="0.0 * degree" phase2="0.0 * degree" phase3="0.0 * degree" id="t69" k1="0.5399934073126 * mole**-1 * kilocalorie" k2="-1.01570109091 * mole**-1 * kilocalorie" k3="-1.102131663183 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0" idivf3="1.0"></Proper>
        <Proper smirks="[*:1]~[#7X2:2]-[#6X4:3]-[*:4]" periodicity1="1" phase1="0.0 * degree" id="t70" k1="-0.5115539743375 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[#6X3:1]=[#7X2,#7X3+1:2]-[#6X4:3]-[#1:4]" periodicity1="1" phase1="0.0 * degree" id="t71" k1="1.647905511831 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[#6X3:1]=[#7X2,#7X3+1:2]-[#6X4:3]-[#6X3,#6X4:4]" periodicity1="1" phase1="0.0 * degree" id="t72" k1="1.066466844349 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]~[#7X3,#7X2-1:2]-[#6X3:3]~[*:4]" periodicity1="2" phase1="180.0 * degree" id="t73" k1="0.7020804385738 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]~[#7X3,#7X2-1:2]-!@[#6X3:3]~[*:4]" periodicity1="2" phase1="180.0 * degree" id="t74" k1="1.37153842814 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]-[#7X3:2]-[#6X3$(*=[#8,#16,#7]):3]~[*:4]" periodicity1="2" periodicity2="1" phase1="180.0 * degree" phase2="0.0 * degree" id="t75" k1="2.169667208431 * mole**-1 * kilocalorie" k2="0.2956358828674 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0"></Proper>
        <Proper smirks="[#1:1]-[#7X3:2]-[#6X3:3]=[#8,#16,#7:4]" periodicity1="2" periodicity2="1" phase1="180.0 * degree" phase2="0.0 * degree" id="t76" k1="0.6343008945455 * mole**-1 * kilocalorie" k2="1.270507666688 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0"></Proper>
        <Proper smirks="[*:1]-[#7X3:2]-!@[#6X3:3](=[#8,#16,#7:4])-[#6,#1]" periodicity1="2" periodicity2="1" phase1="180.0 * degree" phase2="0.0 * degree" id="t77" k1="2.511962403236 * mole**-1 * kilocalorie" k2="-0.5339410353404 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0"></Proper>
        <Proper smirks="[#1:1]-[#7X3:2]-!@[#6X3:3](=[#8,#16,#7:4])-[#6,#1]" periodicity1="2" periodicity2="1" phase1="180.0 * degree" phase2="0.0 * degree" id="t78" k1="1.353570333304 * mole**-1 * kilocalorie" k2="1.210419023716 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0"></Proper>
        <Proper smirks="[*:1]-[#7X3:2]-!@[#6X3:3](=[#8,#16,#7:4])-[#7X3]" periodicity1="2" periodicity2="1" phase1="180.0 * degree" phase2="0.0 * degree" id="t79" k1="0.9750481401663 * mole**-1 * kilocalorie" k2="0.7926272147091 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0"></Proper>
        <Proper smirks="[*:1]-[#7X3;r5:2]-@[#6X3;r5:3]~[*:4]" periodicity1="2" phase1="180.0 * degree" id="t80" k1="1.486934260344 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[#8X1:1]~[#7X3:2]~[#6X3:3]~[*:4]" periodicity1="2" phase1="180.0 * degree" id="t81" k1="0.4873119328207 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]=[#7X2,#7X3+1:2]-[#6X3:3]-[*:4]" periodicity1="2" phase1="180.0 * degree" id="t82" k1="1.016546509378 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]-[#6X3:2]-[#7X3:3](~[#8X1])~[#8X1:4]" periodicity1="2" phase1="180.0 * degree" id="t82a" k1="0.6119311592699 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]=[#7X2,#7X3+1:2]-[#6X3:3]=,:[*:4]" periodicity1="2" phase1="180.0 * degree" id="t83" k1="1.398585409413 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]=,:[#6X3:2]-[#7X3:3](~[#8X1])~[#8X1:4]" periodicity1="2" phase1="180.0 * degree" id="t83a" k1="1.081826366982 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]~[#7X2,#7X3$(*~[#8X1]):2]:[#6X3:3]~[*:4]" periodicity1="2" phase1="180.0 * degree" id="t84" k1="2.205122974863 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[#6X3:1]:[#7X2:2]:[#6X3:3]:[#6X3:4]" periodicity1="2" phase1="180.0 * degree" id="t85" k1="5.590150698229 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]-,:[#6X3:2]=[#7X2:3]-[*:4]" periodicity1="2" phase1="180.0 * degree" id="t86" k1="8.052532732388 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]-[#7X3+1:2]=,:[#6X3:3]-,:[*:4]" periodicity1="2" phase1="180.0 * degree" id="t87" k1="1.088541081003 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]-[#7X3:2]~!@[#6X3:3](~!@[#7X3])~!@[#7X3:4]" periodicity1="2" phase1="180.0 * degree" id="t87a" k1="0.809728596164 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[#16X4,#16X3+0:1]-[#7X2:2]=[#6X3:3]-[#7X3:4]" periodicity1="2" phase1="180.0 * degree" id="t88" k1="3.120029548599 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[#16X4,#16X3+0:1]-[#7X2:2]=[#6X3:3]-[#16X2,#16X3+1:4]" periodicity1="2" phase1="180.0 * degree" id="t89" k1="3.602517565373 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[#7X2:1]~[#7X2:2]-[#6X3:3]~[#6X3:4]" periodicity1="2" phase1="180.0 * degree" id="t90" k1="1.927221534062 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[#7X2:1]~[#7X2:2]-[#6X4:3]-[#6X3:4]" periodicity1="2" phase1="0.0 * degree" id="t91" k1="0.4540929743234 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[#7X2:1]~[#7X2:2]-[#6X4:3]~[#1:4]" periodicity1="2" phase1="0.0 * degree" id="t92" k1="0.3728717936265 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]-[#6X4:2]-[#8X2:3]-[#1:4]" periodicity1="3" phase1="0.0 * degree" id="t93" k1="1.015112840557 * mole**-1 * kilocalorie" idivf1="3.0"></Proper>
        <Proper smirks="[#6X4:1]-[#6X4:2]-[#8X2H1:3]-[#1:4]" periodicity1="3" periodicity2="1" phase1="0.0 * degree" phase2="0.0 * degree" id="t94" k1="0.4475764336372 * mole**-1 * kilocalorie" k2="0.1222812484103 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0"></Proper>
        <Proper smirks="[*:1]-[#6X4:2]-[#8X2H0:3]-[*:4]" periodicity1="3" phase1="0.0 * degree" id="t95" k1="0.7420178199811 * mole**-1 * kilocalorie" idivf1="3.0"></Proper>
        <Proper smirks="[#6X4:1]-[#6X4:2]-[#8X2H0:3]-[#6X4:4]" periodicity1="3" periodicity2="2" phase1="0.0 * degree" phase2="180.0 * degree" id="t96" k1="0.4843877863717 * mole**-1 * kilocalorie" k2="-0.1562321443911 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0"></Proper>
        <Proper smirks="[#6X4:1]-[#6X4:2]-[#8X2:3]-[#6X3:4]" periodicity1="3" periodicity2="1" phase1="0.0 * degree" phase2="180.0 * degree" id="t97" k1="0.1335826791167 * mole**-1 * kilocalorie" k2="0.4558088913692 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0"></Proper>
        <Proper smirks="[#6X4:1]-[#8X2:2]-[#6X4:3]-[#8X2:4]" periodicity1="3" periodicity2="2" periodicity3="1" phase1="0.0 * degree" phase2="180.0 * degree" phase3="180.0 * degree" id="t98" k1="0.1408778897892 * mole**-1 * kilocalorie" k2="0.6692400743421 * mole**-1 * kilocalorie" k3="1.507683794034 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0" idivf3="1.0"></Proper>
        <Proper smirks="[#6X4:1]-[#8X2:2]-[#6X4:3]-[#7X3:4]" periodicity1="3" periodicity2="2" phase1="0.0 * degree" phase2="0.0 * degree" id="t99" k1="-0.09037751194267 * mole**-1 * kilocalorie" k2="0.4558486798387 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0"></Proper>
        <Proper smirks="[#6X3:1]-[#8X2:2]-[#6X4;r3:3]-@[#6X4;r3:4]" periodicity1="1" phase1="0.0 * degree" id="t100" k1="-1.802185889646 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[#6X3:1]-[#8X2:2]-[#6X4;r3:3]-[#1:4]" periodicity1="1" phase1="0.0 * degree" id="t101" k1="-0.6734456899116 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[#1:1]-[#8X2:2]-[#6X4;r3:3]-[#1:4]" periodicity1="3" periodicity2="2" periodicity3="1" phase1="0.0 * degree" phase2="0.0 * degree" phase3="0.0 * degree" id="t102" k1="0.4590201906342 * mole**-1 * kilocalorie" k2="0.05839454154048 * mole**-1 * kilocalorie" k3="-0.277292321901 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0" idivf3="1.0"></Proper>
        <Proper smirks="[#1:1]-[#8X2:2]-[#6X4;r3:3]-[#6X4:4]" periodicity1="3" periodicity2="2" periodicity3="1" phase1="0.0 * degree" phase2="0.0 * degree" phase3="0.0 * degree" id="t103" k1="0.3745298132938 * mole**-1 * kilocalorie" k2="0.1137684363812 * mole**-1 * kilocalorie" k3="-0.4451955496822 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0" idivf3="1.0"></Proper>
        <Proper smirks="[#1:1]-[#8X2:2]-[#6X4;r3:3]-[#6X4;r3:4]" periodicity1="3" periodicity2="2" periodicity3="1" phase1="0.0 * degree" phase2="0.0 * degree" phase3="0.0 * degree" id="t104" k1="0.5242900571996 * mole**-1 * kilocalorie" k2="0.6846369591733 * mole**-1 * kilocalorie" k3="-0.006452869640902 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0" idivf3="1.0"></Proper>
        <Proper smirks="[*:1]~[#6X3:2]-[#8X2:3]-[*:4]" periodicity1="2" phase1="180.0 * degree" id="t105" k1="1.590496267066 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]~[#6X3:2]-[#8X2:3]-[#1:4]" periodicity1="2" phase1="180.0 * degree" id="t106" k1="0.9735099854545 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]~[#6X3:2](=[#8,#16,#7])-[#8X2H0:3]-[*:4]" periodicity1="2" phase1="180.0 * degree" id="t107" k1="3.451843750216 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]~[#6X3:2](=[#8,#16,#7])-[#8:3]-[#1:4]" periodicity1="2" phase1="180.0 * degree" id="t108" k1="3.237327923405 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[#1:1]-[#8X2:2]-[#6X3:3]=[#8X1:4]" periodicity1="2" periodicity2="1" phase1="180.0 * degree" phase2="0.0 * degree" id="t109" k1="2.727969190839 * mole**-1 * kilocalorie" k2="-0.1972522755399 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0"></Proper>
        <Proper smirks="[#8,#16,#7:1]=[#6X3:2]-[#8X2H0:3]-[#6X4:4]" periodicity1="2" periodicity2="1" phase1="180.0 * degree" phase2="180.0 * degree" id="t110" k1="0.281991561038 * mole**-1 * kilocalorie" k2="1.349699723848 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0"></Proper>
        <Proper smirks="[*:1]-[#8X2:2]@[#6X3:3]~[*:4]" periodicity1="2" phase1="180.0 * degree" id="t111" k1="1.728303688589 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]-[#8X2+1:2]=[#6X3:3]-[*:4]" periodicity1="2" phase1="180.0 * degree" id="t112" k1="7.917885966353 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]=[#8X2+1:2]-[#6:3]~[*:4]" periodicity1="2" phase1="180.0 * degree" id="t113" k1="0.6347242530165 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]~[#16:2]=,:[#6:3]~[*:4]" periodicity1="2" phase1="180.0 * degree" id="t114" k1="-1.975848663415 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]-[#16X2,#16X3+1:2]-[#6:3]~[*:4]" periodicity1="2" phase1="180.0 * degree" id="t115" k1="0.3518654073976 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]-[#16X2,#16X3+1:2]-[#6:3]-[#1:4]" periodicity1="3" phase1="0.0 * degree" id="t116" k1="0.411533896633 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[#6X3:1]-@[#16X2,#16X1-1,#16X3+1:2]-@[#6X3,#7X2;r5:3]=@[#6,#7;r5:4]" periodicity1="2" phase1="180.0 * degree" id="t117" k1="8.980846475502 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]~[#16X4,#16X3!+1:2]-[#6X4:3]-[*:4]" periodicity1="3" phase1="0.0 * degree" id="t118" k1="0.2513655781658 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[#6X4:1]-[#16X4,#16X3+0:2]-[#6X4:3]-[#1:4]" periodicity1="1" phase1="0.0 * degree" id="t119" k1="-0.6215542657895 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[#6X4:1]-[#16X4,#16X3+0:2]-[#6X4:3]~[#6X4:4]" periodicity1="3" phase1="0.0 * degree" id="t120" k1="-0.2021544343488 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]~[#16X4,#16X3+0:2]-[#6X3:3]~[*:4]" periodicity1="2" phase1="180.0 * degree" id="t121" k1="0.4345094953046 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[#6:1]-[#16X4,#16X3+0:2]-[#6X3:3]~[*:4]" periodicity1="1" phase1="0.0 * degree" id="t122" k1="0.5802472967885 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]~[#15:2]-[#6:3]-[*:4]" periodicity1="1" phase1="0.0 * degree" id="t123" k1="-10.84539398162 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]~[#15:2]-[#6X4:3]-[*:4]" periodicity1="3" phase1="0.0 * degree" id="t123a" k1="0.1124961486297 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]~[#15:2]-[#6X3:3]~[*:4]" periodicity1="2" periodicity2="3" phase1="0.0 * degree" phase2="0.0 * degree" id="t124" k1="-2.188332979505 * mole**-1 * kilocalorie" k2="0.2817317227731 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0"></Proper>
        <Proper smirks="[*:1]-[#8:2]-[#8:3]-[*:4]" periodicity1="1" phase1="0.0 * degree" id="t125" k1="2.005429751514 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]-[#8:2]-[#8H1:3]-[*:4]" periodicity1="2" phase1="0.0 * degree" id="t126" k1="0.9161192279719 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]~[#8X2:2]-[#7:3]~[*:4]" periodicity1="1" phase1="0.0 * degree" id="t127" k1="1.944492441783 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]-[#8X2r5:2]-;@[#7X3r5:3]~[*:4]" periodicity1="1" phase1="0.0 * degree" id="t128" k1="1.122808273354 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]-[#8X2r5:2]-;@[#7X2r5:3]~[*:4]" periodicity1="1" phase1="0.0 * degree" id="t129" k1="-19.9078720572 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]~[#7X4,#7X3:2]-[#7X4,#7X3:3]~[*:4]" periodicity1="3" phase1="0.0 * degree" id="t130" k1="0.8455687957516 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[#1:1]-[#7X4,#7X3:2]-[#7X4,#7X3:3]-[#1:4]" periodicity1="3" periodicity2="2" periodicity3="1" phase1="0.0 * degree" phase2="0.0 * degree" phase3="0.0 * degree" id="t131" k1="0.09107282863165 * mole**-1 * kilocalorie" k2="0.6514412536842 * mole**-1 * kilocalorie" k3="0.4354106055474 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0" idivf3="1.0"></Proper>
        <Proper smirks="[#6X4:1]-[#7X4,#7X3:2]-[#7X4,#7X3:3]-[#1:4]" periodicity1="3" periodicity2="2" periodicity3="1" phase1="0.0 * degree" phase2="0.0 * degree" phase3="0.0 * degree" id="t132" k1="0.3850736319737 * mole**-1 * kilocalorie" k2="0.3408378467109 * mole**-1 * kilocalorie" k3="0.6287427416993 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0" idivf3="1.0"></Proper>
        <Proper smirks="[#6X4:1]-[#7X4,#7X3:2]-[#7X4,#7X3:3]-[#6X4:4]" periodicity1="3" periodicity2="2" periodicity3="1" phase1="0.0 * degree" phase2="0.0 * degree" phase3="0.0 * degree" id="t133" k1="-0.009788450190304 * mole**-1 * kilocalorie" k2="0.3057256707337 * mole**-1 * kilocalorie" k3="0.6709990493073 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0" idivf3="1.0"></Proper>
        <Proper smirks="[*:1]-[#7X4,#7X3:2]-[#7X3$(*~[#6X3,#6X2]):3]~[*:4]" periodicity1="1" phase1="0.0 * degree" id="t134" k1="-1.097759861665 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]-[#7X3$(*-[#6X3,#6X2]):2]-[#7X3$(*-[#6X3,#6X2]):3]-[*:4]" periodicity1="1" phase1="0.0 * degree" id="t135" k1="-0.6701788668107 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]-[#7X3$(*-[#6X3,#6X2])r5:2]-@[#7X3$(*-[#6X3,#6X2])r5:3]~[*:4]" periodicity1="2" phase1="180.0 * degree" id="t136" k1="-0.4668933845214 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]@[#7X2:2]@[#7X2:3]@[#7X2,#6X3:4]" periodicity1="1" phase1="180.0 * degree" id="t137" k1="4.605325210702 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]~[#7X2:2]-[#7X3:3]~[*:4]" periodicity1="3" periodicity2="2" phase1="0.0 * degree" phase2="180.0 * degree" id="t138" k1="-0.4008170401113 * mole**-1 * kilocalorie" k2="2.066737337597 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0"></Proper>
        <Proper smirks="[*:1]~[#7X2:2]-[#7X4:3]~[*:4]" periodicity1="3" periodicity2="2" phase1="0.0 * degree" phase2="180.0 * degree" id="t138a" k1="-0.09510851427019 * mole**-1 * kilocalorie" k2="1.554435554256 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0"></Proper>
        <Proper smirks="[*:1]=[#7X2:2]-[#7X2:3]=[*:4]" periodicity1="2" phase1="180.0 * degree" id="t139" k1="4.289789940629 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]~[#7X2:2]=,:[#7X2:3]~[*:4]" periodicity1="2" phase1="180.0 * degree" id="t140" k1="15.78931647465 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]~[#7X3+1:2]=,:[#7X2:3]~[*:4]" periodicity1="2" phase1="180.0 * degree" id="t141" k1="10.57958597957 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]~[#7x3:2]-[#7x3,#6x3:3]~[*:4]" periodicity1="1" phase1="0.0 * degree" id="t141a" k1="-3.906902709944 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]~[#7x2:2]-[#7x3:3]~[*:4]" periodicity1="3" periodicity2="2" phase1="0.0 * degree" phase2="180.0 * degree" id="t141b" k1="1.114119889873 * mole**-1 * kilocalorie" k2="0.5022068651646 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0"></Proper>
        <Proper smirks="[*:1]~[#6x3:2](~[#7,#8,#16])-[#6x3:3]~[*:4]" periodicity1="1" phase1="0.0 * degree" id="t141c" k1="-3.525605054758 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]-[#16X2,#16X3+1:2]-[!#6:3]~[*:4]" periodicity1="2" phase1="180.0 * degree" id="t142" k1="-0.7715979690747 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]~[#16X4,#16X3+0:2]-[#7:3]~[*:4]" periodicity1="1" periodicity2="2" periodicity3="3" phase1="180.0 * degree" phase2="0.0 * degree" phase3="0.0 * degree" id="t143" k1="-1.6882659825 * mole**-1 * kilocalorie" k2="0.3191499888753 * mole**-1 * kilocalorie" k3="0.2193673170111 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0" idivf3="1.0"></Proper>
        <Proper smirks="[#6X4:1]-[#16X4,#16X3+0:2]-[#7X4,#7X3:3]-[#1:4]" periodicity1="1" phase1="0.0 * degree" id="t144" k1="-0.4293851957275 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[#6X3:1]-[#16X4,#16X3+0:2]-[#7X4,#7X3:3]-[#1:4]" periodicity1="3" phase1="0.0 * degree" id="t145" k1="0.1979639349968 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[#6X4:1]-[#16X4,#16X3+0:2]-[#7X4,#7X3:3]-[#6X4:4]" periodicity1="1" periodicity2="3" phase1="0.0 * degree" phase2="0.0 * degree" id="t146" k1="0.687851489207 * mole**-1 * kilocalorie" k2="0.3710469353726 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0"></Proper>
        <Proper smirks="[#6X3:1]-[#16X4,#16X3+0:2]-[#7X4,#7X3:3]-[#6X4:4]" periodicity1="3" periodicity2="2" periodicity3="1" phase1="0.0 * degree" phase2="0.0 * degree" phase3="0.0 * degree" id="t147" k1="0.9015016181202 * mole**-1 * kilocalorie" k2="0.4955057836023 * mole**-1 * kilocalorie" k3="1.219367957863 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0" idivf3="1.0"></Proper>
        <Proper smirks="[#8X1:1]~[#16X4,#16X3+0:2]-[#7X4,#7X3:3]-[#1:4]" periodicity1="1" periodicity2="3" phase1="180.0 * degree" phase2="0.0 * degree" id="t148" k1="-0.6905827233348 * mole**-1 * kilocalorie" k2="0.2618727450261 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0"></Proper>
        <Proper smirks="[#8X1:1]~[#16X4,#16X3+0:2]-[#7X4,#7X3:3]-[#6X4:4]" periodicity1="3" periodicity2="2" periodicity3="1" phase1="0.0 * degree" phase2="180.0 * degree" phase3="0.0 * degree" id="t149" k1="0.0142928891611 * mole**-1 * kilocalorie" k2="0.5969431944992 * mole**-1 * kilocalorie" k3="1.529139493109 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0" idivf3="1.0"></Proper>
        <Proper smirks="[#6X3:1]-[#16X4,#16X3+0:2]-[#7X3:3]-[#6X3:4]" periodicity1="3" periodicity2="2" periodicity3="1" phase1="0.0 * degree" phase2="0.0 * degree" phase3="0.0 * degree" id="t150" k1="0.4603759126772 * mole**-1 * kilocalorie" k2="0.587206663536 * mole**-1 * kilocalorie" k3="-0.5000049198338 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0" idivf3="1.0"></Proper>
        <Proper smirks="[#6X4:1]-[#16X4,#16X3+0:2]-[#7X3:3]-[#6X3:4]" periodicity1="3" periodicity2="2" phase1="90.0 * degree" phase2="0.0 * degree" id="t151" k1="-0.518563003676 * mole**-1 * kilocalorie" k2="1.275130575571 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0"></Proper>
        <Proper smirks="[#8X1:1]~[#16X4,#16X3+0:2]-[#7X3:3]-[#6X3:4]" periodicity1="1" phase1="0.0 * degree" id="t152" k1="-0.06694770921212 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[#8X1:1]~[#16X4,#16X3+0:2]-[#7X3:3]-[#7X2:4]" periodicity1="1" phase1="0.0 * degree" id="t153" k1="2.923303924453 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]~[#16X4,#16X3+0:2]=,:[#7X2:3]-,:[*:4]" periodicity1="1" phase1="0.0 * degree" id="t154" k1="3.271521150662 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[#6X4:1]-[#16X4,#16X3+0:2]-[#7X2:3]~[#6X3:4]" periodicity1="6" periodicity2="5" periodicity3="4" periodicity4="3" periodicity5="2" periodicity6="1" phase1="0.0 * degree" phase2="0.0 * degree" phase3="0.0 * degree" phase4="0.0 * degree" phase5="180.0 * degree" phase6="0.0 * degree" id="t155" k1="-0.2727080925837 * mole**-1 * kilocalorie" k2="0.0294466056591 * mole**-1 * kilocalorie" k3="0.1583146108926 * mole**-1 * kilocalorie" k4="0.3501787760781 * mole**-1 * kilocalorie" k5="-0.3662581658181 * mole**-1 * kilocalorie" k6="2.14698681307 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0" idivf3="1.0" idivf4="1.0" idivf5="1.0" idivf6="1.0"></Proper>
        <Proper smirks="[#8X1:1]~[#16X4,#16X3+0:2]-[#7X2:3]~[#6X3:4]" periodicity1="6" periodicity2="5" periodicity3="4" periodicity4="2" periodicity5="3" periodicity6="1" phase1="0.0 * degree" phase2="0.0 * degree" phase3="180.0 * degree" phase4="180.0 * degree" phase5="180.0 * degree" phase6="0.0 * degree" id="t156" k1="0.142362680367 * mole**-1 * kilocalorie" k2="-0.3230321042888 * mole**-1 * kilocalorie" k3="0.2524948234308 * mole**-1 * kilocalorie" k4="0.6528364511879 * mole**-1 * kilocalorie" k5="1.196977446782 * mole**-1 * kilocalorie" k6="1.167785785382 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0" idivf3="1.0" idivf4="1.0" idivf5="1.0" idivf6="1.0"></Proper>
        <Proper smirks="[*:1]~[#16X4,#16X3+0:2]-[#8X2:3]-[*:4]" periodicity1="1" periodicity2="2" phase1="0.0 * degree" phase2="0.0 * degree" id="t157" k1="3.0431043823 * mole**-1 * kilocalorie" k2="-0.3593529791623 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0"></Proper>
        <Proper smirks="[*:1]-[#16X2,#16X3+1:2]-[#16X2,#16X3+1:3]-[*:4]" periodicity1="2" periodicity2="3" phase1="0.0 * degree" phase2="0.0 * degree" id="t158" k1="3.627584495399 * mole**-1 * kilocalorie" k2="0.3719185183686 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0"></Proper>
        <Proper smirks="[*:1]-[#8X2:2]-[#15:3]~[*:4]" periodicity1="3" periodicity2="1" periodicity3="2" phase1="0.0 * degree" phase2="0.0 * degree" phase3="0.0 * degree" id="t159" k1="0.4282832518251 * mole**-1 * kilocalorie" k2="9.382773933827 * mole**-1 * kilocalorie" k3="-1.899750980497 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0" idivf3="1.0"></Proper>
        <Proper smirks="[#8X2:1]-[#15:2]-[#8X2:3]-[#6X4:4]" periodicity1="3" periodicity2="2" periodicity3="1" phase1="0.0 * degree" phase2="0.0 * degree" phase3="0.0 * degree" id="t160" k1="-0.7591901835796 * mole**-1 * kilocalorie" k2="-1.569732559885 * mole**-1 * kilocalorie" k3="8.204059562093 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0" idivf3="1.0"></Proper>
        <Proper smirks="[*:1]~[#7:2]-[#15:3]~[*:4]" periodicity1="2" phase1="180.0 * degree" id="t161" k1="1.265542041067 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]-[#7:2]-[#15:3]=[*:4]" periodicity1="2" periodicity2="3" phase1="180.0 * degree" phase2="0.0 * degree" id="t162" k1="2.012364097137 * mole**-1 * kilocalorie" k2="0.4145088037321 * mole**-1 * kilocalorie" idivf1="1.0" idivf2="1.0"></Proper>
        <Proper smirks="[#6X3:1]-[#7:2]-[#15:3]=[*:4]" periodicity1="1" phase1="0.0 * degree" id="t163" k1="-1.94148850547 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]~[#7:2]=[#15:3]~[*:4]" periodicity1="3" phase1="0.0 * degree" id="t164" k1="-0.9670595402247 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]-[*:2]#[*:3]-[*:4]" periodicity1="1" phase1="0.0 * degree" id="t165" k1="0.0 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]~[*:2]-[*:3]#[*:4]" periodicity1="1" phase1="0.0 * degree" id="t166" k1="0.0 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
        <Proper smirks="[*:1]~[*:2]=[#6,#7,#16,#15;X2:3]=[*:4]" periodicity1="1" phase1="0.0 * degree" id="t167" k1="0.0 * mole**-1 * kilocalorie" idivf1="1.0"></Proper>
    </ProperTorsions>
    <ImproperTorsions version="0.3" potential="k*(1+cos(periodicity*theta-phase))" default_idivf="auto">
        <Improper smirks="[*:1]~[#6X3:2](~[*:3])~[*:4]" periodicity1="2" phase1="180.0 * degree" k1="5.230790565314 * mole**-1 * kilocalorie" id="i1"></Improper>
        <Improper smirks="[*:1]~[#6X3:2](~[#8X1:3])~[#8:4]" periodicity1="2" phase1="180.0 * degree" k1="12.91569668378 * mole**-1 * kilocalorie" id="i2"></Improper>
        <Improper smirks="[*:1]~[#7X3$(*~[#15,#16](!-[*])):2](~[*:3])~[*:4]" periodicity1="2" phase1="180.0 * degree" k1="13.7015994787 * mole**-1 * kilocalorie" id="i3"></Improper>
        <Improper smirks="[*:1]~[#7X3$(*~[#6X3]):2](~[*:3])~[*:4]" periodicity1="2" phase1="180.0 * degree" k1="1.256262500552 * mole**-1 * kilocalorie" id="i4"></Improper>
        <Improper smirks="[*:1]~[#7X3$(*~[#7X2]):2](~[*:3])~[*:4]" periodicity1="2" phase1="180.0 * degree" k1="-2.341750027278 * mole**-1 * kilocalorie" id="i5"></Improper>
        <Improper smirks="[*:1]~[#7X3$(*@1-[*]=,:[*][*]=,:[*]@1):2](~[*:3])~[*:4]" periodicity1="2" phase1="180.0 * degree" k1="16.00585907359 * mole**-1 * kilocalorie" id="i6"></Improper>
        <Improper smirks="[*:1]~[#6X3:2](=[#7X2,#7X3+1:3])~[#7:4]" periodicity1="2" phase1="180.0 * degree" k1="10.12246975417 * mole**-1 * kilocalorie" id="i7"></Improper>
    </ImproperTorsions>
    <vdW version="0.3" potential="Lennard-Jones-12-6" combining_rules="Lorentz-Berthelot" scale12="0.0" scale13="0.0" scale14="0.5" scale15="1.0" cutoff="9.0 * angstrom" switch_width="1.0 * angstrom" method="cutoff">
        <Atom smirks="[#1:1]" epsilon="0.0157 * mole**-1 * kilocalorie" id="n1" rmin_half="0.6 * angstrom"></Atom>
        <Atom smirks="[#1:1]-[#6X4]" epsilon="0.01577948280971 * mole**-1 * kilocalorie" id="n2" rmin_half="1.48419980825 * angstrom"></Atom>
        <Atom smirks="[#1:1]-[#6X4]-[#7,#8,#9,#16,#17,#35]" epsilon="0.01640924602775 * mole**-1 * kilocalorie" id="n3" rmin_half="1.449786411317 * angstrom"></Atom>
        <Atom smirks="[#1:1]-[#6X4](-[#7,#8,#9,#16,#17,#35])-[#7,#8,#9,#16,#17,#35]" epsilon="0.0157 * mole**-1 * kilocalorie" id="n4" rmin_half="1.287 * angstrom"></Atom>
        <Atom smirks="[#1:1]-[#6X4](-[#7,#8,#9,#16,#17,#35])(-[#7,#8,#9,#16,#17,#35])-[#7,#8,#9,#16,#17,#35]" epsilon="0.0157 * mole**-1 * kilocalorie" id="n5" rmin_half="1.187 * angstrom"></Atom>
        <Atom smirks="[#1:1]-[#6X4]~[*+1,*+2]" epsilon="0.0157 * mole**-1 * kilocalorie" id="n6" rmin_half="1.1 * angstrom"></Atom>
        <Atom smirks="[#1:1]-[#6X3]" epsilon="0.01561134320353 * mole**-1 * kilocalorie" id="n7" rmin_half="1.443812569645 * angstrom"></Atom>
        <Atom smirks="[#1:1]-[#6X3]~[#7,#8,#9,#16,#17,#35]" epsilon="0.01310699839698 * mole**-1 * kilocalorie" id="n8" rmin_half="1.377051329051 * angstrom"></Atom>
        <Atom smirks="[#1:1]-[#6X3](~[#7,#8,#9,#16,#17,#35])~[#7,#8,#9,#16,#17,#35]" epsilon="0.01479744504464 * mole**-1 * kilocalorie" id="n9" rmin_half="1.370482808197 * angstrom"></Atom>
        <Atom smirks="[#1:1]-[#6X2]" epsilon="0.015 * mole**-1 * kilocalorie" id="n10" rmin_half="1.459 * angstrom"></Atom>
        <Atom smirks="[#1:1]-[#7]" epsilon="0.01409081474669 * mole**-1 * kilocalorie" id="n11" rmin_half="0.6192778454102 * angstrom"></Atom>
        <Atom smirks="[#1:1]-[#8]" epsilon="1.232599966667e-05 * mole**-1 * kilocalorie" id="n12" rmin_half="0.2999999999997 * angstrom"></Atom>
        <Atom smirks="[#1:1]-[#16]" epsilon="0.0157 * mole**-1 * kilocalorie" id="n13" rmin_half="0.6 * angstrom"></Atom>
        <Atom smirks="[#6:1]" epsilon="0.0868793154488 * mole**-1 * kilocalorie" id="n14" rmin_half="1.953447017081 * angstrom"></Atom>
        <Atom smirks="[#6X2:1]" epsilon="0.21 * mole**-1 * kilocalorie" id="n15" rmin_half="1.908 * angstrom"></Atom>
        <Atom smirks="[#6X4:1]" epsilon="0.1088406109251 * mole**-1 * kilocalorie" id="n16" rmin_half="1.896698071741 * angstrom"></Atom>
        <Atom smirks="[#8:1]" epsilon="0.2102061007896 * mole**-1 * kilocalorie" id="n17" rmin_half="1.706036917087 * angstrom"></Atom>
        <Atom smirks="[#8X2H0+0:1]" epsilon="0.1684651402602 * mole**-1 * kilocalorie" id="n18" rmin_half="1.697783613804 * angstrom"></Atom>
        <Atom smirks="[#8X2H1+0:1]" epsilon="0.2094735324129 * mole**-1 * kilocalorie" id="n19" rmin_half="1.682099169199 * angstrom"></Atom>
        <Atom smirks="[#7:1]" epsilon="0.1676915150424 * mole**-1 * kilocalorie" id="n20" rmin_half="1.799798315098 * angstrom"></Atom>
        <Atom smirks="[#16:1]" epsilon="0.25 * mole**-1 * kilocalorie" id="n21" rmin_half="2.0 * angstrom"></Atom>
        <Atom smirks="[#15:1]" epsilon="0.2 * mole**-1 * kilocalorie" id="n22" rmin_half="2.1 * angstrom"></Atom>
        <Atom smirks="[#9:1]" epsilon="0.061 * mole**-1 * kilocalorie" id="n23" rmin_half="1.75 * angstrom"></Atom>
        <Atom smirks="[#17:1]" epsilon="0.2656001046527 * mole**-1 * kilocalorie" id="n24" rmin_half="1.85628721824 * angstrom"></Atom>
        <Atom smirks="[#35:1]" epsilon="0.3218986365974 * mole**-1 * kilocalorie" id="n25" rmin_half="1.969806594135 * angstrom"></Atom>
        <Atom smirks="[#53:1]" epsilon="0.4 * mole**-1 * kilocalorie" id="n26" rmin_half="2.35 * angstrom"></Atom>
        <Atom smirks="[#3+1:1]" epsilon="0.0279896 * mole**-1 * kilocalorie" id="n27" rmin_half="1.025 * angstrom"></Atom>
        <Atom smirks="[#11+1:1]" epsilon="0.0874393 * mole**-1 * kilocalorie" id="n28" rmin_half="1.369 * angstrom"></Atom>
        <Atom smirks="[#19+1:1]" epsilon="0.1936829 * mole**-1 * kilocalorie" id="n29" rmin_half="1.705 * angstrom"></Atom>
        <Atom smirks="[#37+1:1]" epsilon="0.3278219 * mole**-1 * kilocalorie" id="n30" rmin_half="1.813 * angstrom"></Atom>
        <Atom smirks="[#55+1:1]" epsilon="0.4065394 * mole**-1 * kilocalorie" id="n31" rmin_half="1.976 * angstrom"></Atom>
        <Atom smirks="[#9X0-1:1]" epsilon="0.003364 * mole**-1 * kilocalorie" id="n32" rmin_half="2.303 * angstrom"></Atom>
        <Atom smirks="[#17X0-1:1]" epsilon="0.035591 * mole**-1 * kilocalorie" id="n33" rmin_half="2.513 * angstrom"></Atom>
        <Atom smirks="[#35X0-1:1]" epsilon="0.0586554 * mole**-1 * kilocalorie" id="n34" rmin_half="2.608 * angstrom"></Atom>
        <Atom smirks="[#53X0-1:1]" epsilon="0.0536816 * mole**-1 * kilocalorie" id="n35" rmin_half="2.86 * angstrom"></Atom>
        <Atom smirks="[#1]-[#8X2H2+0:1]-[#1]" epsilon="0.1521 * mole**-1 * kilocalorie" id="n-tip3p-O" sigma="3.1507 * angstrom"></Atom>
        <Atom smirks="[#1:1]-[#8X2H2+0]-[#1]" epsilon="0 * mole**-1 * kilocalorie" id="n-tip3p-H" sigma="1 * angstrom"></Atom>
    </vdW>
    <Electrostatics version="0.3" scale12="0.0" scale13="0.0" scale14="0.8333333333" scale15="1.0" cutoff="9.0 * angstrom" switch_width="0.0 * angstrom" method="PME"></Electrostatics>
    <LibraryCharges version="0.3">
    </LibraryCharges>
    <ToolkitAM1BCC version="0.3"></ToolkitAM1BCC>
</SMIRNOFF>"""

def load_sage_csys():
    """
    Load the OpenFF Sage 2.1 force field
    """
    global xml

    fd, ff_fname = tempfile.mkstemp(prefix=".offxml")
    with os.fdopen(fd, 'w') as f:
        f.write(xml)
    gcd = codec_rdkit.graph_codec_rdkit()
    labeler = hierarchy_assign_rdkit.smarts_hierarchy_assignment_rdkit()
    pcp = perception.perception_model(gcd, labeler)
    csys = smirnoff_models.smirnoff_load(ff_fname, pcp)

    return csys

if __name__ == "__main__":
    run()
