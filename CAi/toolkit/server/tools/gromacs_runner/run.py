import json
import subprocess
from pathlib import Path

# 全局指定你的 GROMACS 环境路径
GMXRC_PATH = "/mnt/shared-storage-gpfs2/chenjiangyu-gpfs02/gromacs-2026.2/bin/GMXRC"

def run_gmx_command(command: str):
    """
    底层核心封装：先 source 环境，再执行 gmx 命令。
    必须使用 /bin/bash 执行，才能识别 source 命令。
    """
    full_cmd = f"source {GMXRC_PATH} && {command}"
    print(f"正在执行命令: {full_cmd}")
    
    # capture_output=True 会捕获日志，防止污染我们的 result.json
    result = subprocess.run(
        full_cmd, 
        shell=True, 
        executable="/bin/bash", 
        capture_output=True, 
        text=True
    )
    
    if result.returncode != 0:
        # GROMACS 报错通常在 stderr 的最后面，这里可以直接抛出给外层捕获
        raise RuntimeError(f"GROMACS 执行失败:\n{result.stderr[-1000:]}")
        
    return result.stdout

def process_data(params):
    """
    路由分发器：根据前端传入的 'step' 决定执行 GROMACS 的哪一步
    """
    step = params.get("step")
    if not step:
        raise ValueError("必须提供参数: step (例如 prep, solvate, minimize 等)")

    # 1. 拓扑与坐标准备
    if step == "prep":
        pdb_in = params.get("input_pdb", "protein.pdb")
        forcefield = params.get("ff", "amber99sb-ildn")
        water = params.get("water", "tip3p")
        
        cmd = f"gmx pdb2gmx -f {pdb_in} -o processed.gro -p topol.top -ff {forcefield} -water {water} -ignh"
        log = run_gmx_command(cmd)
        
        # 定义好盒子尺寸
        run_gmx_command("gmx editconf -f processed.gro -o box.gro -c -d 1.0 -bt cubic")
        return {"output_gro": "box.gro", "output_top": "topol.top", "message": "拓扑建立完成"}

    # 2. 溶剂化与加离子
    elif step == "solvate":
        run_gmx_command("gmx solvate -cp box.gro -cs spc216.gro -o solvated.gro -p topol.top")
        
        # 预处理生成 tpr 以便加离子 (需外部提供 ions.mdp)
        run_gmx_command("gmx grompp -f ions.mdp -c solvated.gro -p topol.top -o ions.tpr -maxwarn 1")
        # 自动选组 13 (SOL) 替换为离子
        run_gmx_command("echo 13 | gmx genion -s ions.tpr -o ionized.gro -p topol.top -pname NA -nname CL -neutral")
        return {"output_gro": "ionized.gro", "message": "溶剂化及离子添加完成"}

    # 3. 能量最小化 (EM)
    elif step == "minimize":
        run_gmx_command("gmx grompp -f minim.mdp -c ionized.gro -p topol.top -o em.tpr")
        run_gmx_command("gmx mdrun -v -deffnm em")
        return {"output_gro": "em.gro", "message": "能量最小化完成"}

    # 4. 平衡 (NVT / NPT)
    elif step == "equilibrate":
        mode = params.get("mode", "nvt") # nvt 或 npt
        input_gro = params.get("input_gro", "em.gro")
        run_gmx_command(f"gmx grompp -f {mode}.mdp -c {input_gro} -r {input_gro} -p topol.top -o {mode}.tpr")
        run_gmx_command(f"gmx mdrun -v -deffnm {mode}")
        return {"output_gro": f"{mode}.gro", "message": f"{mode.upper()} 平衡完成"}

    # 5. 生产模拟 (MD)
    elif step == "production":
        input_gro = params.get("input_gro", "npt.gro")
        run_gmx_command(f"gmx grompp -f md.mdp -c {input_gro} -t npt.cpt -p topol.top -o md_0_1.tpr")
        # 利用工具系统的 gpu 配置，自动使用 GPU 跑模拟
        run_gmx_command("gmx mdrun -v -deffnm md_0_1 -nb gpu")
        return {"output_xtc": "md_0_1.xtc", "message": "生产模拟完成"}

    else:
        raise ValueError(f"未知的 GROMACS 步骤: {step}")

def main():
    result_payload = {}
    try:
        # 严格按照规范：从沙盒目录读取 params.json
        with open("params.json", "r", encoding="utf-8") as f:
            params = json.load(f)

        data = process_data(params)
        result_payload = {"success": True, "data": data}

    except Exception as e:
        result_payload = {"success": False, "error": str(e)}

    # 严格按照规范：将结果写入 result.json
    with open("result.json", "w", encoding="utf-8") as f:
        json.dump(result_payload, f, ensure_ascii=False, indent=2)

    if result_payload.get("success"):
        print("✅ GROMACS 工具执行成功")
    else:
        print(f"❌ GROMACS 工具执行失败: {result_payload.get('error')}")

if __name__ == "__main__":
    main()