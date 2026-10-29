// 查询 Minecraft 基岩版服务器状态
// 用法: node mc_ping.js <host> <port>   （先在本目录执行 npm install）
const { ping } = require('bedrock-protocol');
const host = process.argv[2] || '';
const port = parseInt(process.argv[3] || '19132', 10);
if (!host) {
  console.log(JSON.stringify({ error: 'MC server host not configured' }));
  process.exit(0);
}
const t0 = Date.now();
ping({ host, port }).then(r => {
  r.latencyMs = Date.now() - t0;
  console.log(JSON.stringify(r));
}).catch(e => {
  console.log(JSON.stringify({ error: e.message }));
});
