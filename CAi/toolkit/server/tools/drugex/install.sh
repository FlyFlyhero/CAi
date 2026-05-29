#!/usr/bin/env bash
# ==============================================================================
# DrugEx 一键安装脚本
# ==============================================================================
# 用法：cd CAi/additional_tools/server/tools/drugex && bash install.sh
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="${SCRIPT_DIR}/DrugEx"
EXAMPLES="${BASE_DIR}/tutorial/CLI/examples"
CONDA_ENV="drugex"

echo "===== 第 1 步：创建 conda 环境 ====="
if conda env list | grep -q "^${CONDA_ENV} "; then
    echo "conda 环境 '${CONDA_ENV}' 已存在，跳过。"
else
    conda create -n "${CONDA_ENV}" python=3.10 -y
fi

echo ""
echo "===== 第 2 步：克隆 DrugEx 源码 ====="
if [ ! -d "${BASE_DIR}" ]; then
    git clone https://github.com/CDDLeiden/DrugEx.git "${BASE_DIR}"
else
    echo "DrugEx 源码已存在，跳过。"
fi

echo ""
echo "===== 第 3 步：安装 DrugEx ====="
conda run -n "${CONDA_ENV}" --cwd "${BASE_DIR}" \
    pip install "drugex[qsprpred] @ git+https://github.com/CDDLeiden/DrugEx.git@master"

echo ""
echo "===== 第 4 步：下载模型和数据集 ====="

# --- 4a. 用 curl 下载 3 个 Zenodo 模型 zip（绕过 requests 重定向问题）---
mkdir -p "${EXAMPLES}/models/pretrained/smiles-rnn/Papyrus05.5_smiles_rnn_PT"
mkdir -p "${EXAMPLES}/models/pretrained/graph-trans/Papyrus05.5_graph_trans_PT"
mkdir -p "${EXAMPLES}/models/qsar"

DL_DIR="${EXAMPLES}/_downloads"
mkdir -p "${DL_DIR}"

download_and_unzip() {
    local url="$1"
    local zip_path="$2"
    local extract_dir="$3"

    if [ -f "${zip_path}" ] && file "${zip_path}" | grep -q "Zip archive"; then
        echo "  已存在: $(basename "${zip_path}")，跳过下载。"
    else
        echo "  下载: $(basename "${zip_path}")..."
        curl -L --progress-bar --retry 3 --max-time 600 -o "${zip_path}" "${url}"
    fi
    echo "  解压 → ${extract_dir}"
    unzip -o -q "${zip_path}" -d "${extract_dir}"
}

echo "下载预训练模型（smiles-rnn）..."
download_and_unzip \
    "https://zenodo.org/record/7378923/files/DrugEx_v2_PT_Papyrus05.5.zip?download=1" \
    "${DL_DIR}/DrugEx_v2_PT_Papyrus05.5.zip" \
    "${EXAMPLES}/models/pretrained/smiles-rnn/Papyrus05.5_smiles_rnn_PT"

echo "下载预训练模型（graph-trans）..."
download_and_unzip \
    "https://zenodo.org/record/7085421/files/DrugEx_PT_Papyrus05.5.zip?download=1" \
    "${DL_DIR}/DrugEx_PT_Papyrus05.5.zip" \
    "${EXAMPLES}/models/pretrained/graph-trans/Papyrus05.5_graph_trans_PT"

echo "下载 QSAR 模型..."
download_and_unzip \
    "https://zenodo.org/records/13283924/files/A2AR_tutorial_models.zip?download=1" \
    "${DL_DIR}/A2AR_tutorial_models.zip" \
    "${EXAMPLES}/models/qsar"

# 复制 vocab 文件
SMILES_VOC="${EXAMPLES}/models/pretrained/smiles-rnn/Papyrus05.5_smiles_rnn_PT/Papyrus05.5_smiles_rnn_PT.vocab"
if [ -f "${SMILES_VOC}" ]; then
    mkdir -p "${EXAMPLES}/data"
    cp "${SMILES_VOC}" "${EXAMPLES}/data/Papyrus05.5_smiles_voc.txt"
fi

# --- 4b. 用 drugex.download 下载 Papyrus 数据集（模型已存在会自动跳过）---
echo ""
echo "下载 Papyrus 数据集..."
conda run -n "${CONDA_ENV}" --cwd "${BASE_DIR}" \
    python -m drugex.download -o "${EXAMPLES}"

echo ""
echo "===== 第 5 步：预处理数据集 ====="
conda run -n "${CONDA_ENV}" --cwd "${BASE_DIR}" \
    python -m drugex.dataset \
    -b "${EXAMPLES}" \
    -i A2AR_LIGANDS.tsv \
    -mc SMILES \
    -o arl \
    -mt graph \
    -np 1

echo ""
echo "===== 第 6 步：写入路径配置 ====="
cat > "${SCRIPT_DIR}/paths.json" <<EOF
{
    "base_dir": "${EXAMPLES}",
    "drugex_repo_dir": "${BASE_DIR}",
    "pretrained_model_graph": "${EXAMPLES}/models/pretrained/graph-trans/Papyrus05.5_graph_trans_PT/Papyrus05.5_graph_trans_PT.pkg",
    "pretrained_model_rnn": "${EXAMPLES}/models/pretrained/smiles-rnn/Papyrus05.5_smiles_rnn_PT/Papyrus05.5_smiles_rnn_PT.pkg",
    "qsar_model_a2ar": "${EXAMPLES}/models/qsar/A2AR_RandomForestClassifier/A2AR_RandomForestClassifier_meta.json",
    "default_input_prefix": "arl",
    "default_test_fragments": "arl_test_graph.txt"
}
EOF

echo ""
echo "===== 安装完成！====="
echo ""
echo "数据目录: ${EXAMPLES}"
echo "路径配置: ${SCRIPT_DIR}/paths.json"
echo ""
echo "测试步骤："
echo "  1. 重启 app.py"
echo "  2. curl http://127.0.0.1:8001/tools"
echo "  3. python send_request_template.py generate"