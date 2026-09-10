/** @type {import('next').NextConfig} */
const nextConfig = {
  // Desligado por causa do SSE. O Next comprime as respostas do proxy por
  // padrão, e gzip sobre `text/event-stream` **bufferiza**: o backend emitia os
  // eventos na hora, o navegador não recebia nenhum, e a tela de varredura
  // ficava em "aguardando o primeiro passo" com o cronômetro correndo.
  //
  // O sintoma enganava porque `curl` não pede compressão por padrão — pelo
  // terminal o stream chegava perfeito, e só pelo navegador (que sempre manda
  // `Accept-Encoding: gzip`) é que sumia. Verificado nos dois lados: direto na
  // :8000 a resposta não tem `Content-Encoding`; pelo proxy, tem.
  //
  // O custo é irrelevante aqui: as respostas são JSON pequeno e o front é um
  // dashboard interno. Em produção atrás de CDN, a compressão é dela.
  compress: false,

  async rewrites() {
    // A API roda em :8000. O proxy evita CORS e mantém o front agnóstico de host:
    // trocar o backend é mudar RADAR_API_URL, não recompilar o cliente.
    return [
      { source: "/api/:path*", destination: `${process.env.RADAR_API_URL ?? "http://localhost:8000"}/:path*` },
    ];
  },
};
export default nextConfig;
