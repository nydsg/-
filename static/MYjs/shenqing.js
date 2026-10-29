async function applyShenheyuan() {
    const btn = document.getElementById('applyBtn');
    const resultDiv = document.getElementById('resultMsg');
    
    btn.disabled = true;
    btn.textContent = '申请中...';
    resultDiv.innerHTML = '';
    
    try {
        // 安全修复：/shenheyuan 会写入审核员表，后端已改为只接受 POST（防止 GET 型 CSRF）
        const response = await fetch('/shenheyuan', { method: 'POST' });
        const data = await response.json();  // 即使是 401/403，也要解析 JSON
        
        if (data.status === 'success') {
            resultDiv.innerHTML = `<p style="color: green;">✅ ${data.message}</p>`;
            btn.textContent = '申请成功';
        } else {
            //  显示后端的错误消息
            resultDiv.innerHTML = `<p style="color: orange;">⚠️ ${data.message}</p>`;
            btn.disabled = false;
            btn.textContent = '重新申请';
        }
    } catch (error) {
        console.error('请求失败:', error);
        resultDiv.innerHTML = `<p style="color: red;">❌ 网络错误，请稍后重试</p>`;
        btn.disabled = false;
        btn.textContent = '提交申请';
    }
}