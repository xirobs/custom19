/* Carga Leaflet y renderiza un mapa de prueba si el contenedor no existe. */
(function initLeafletMap() {
    const leafletScript = document.createElement('script');
    leafletScript.src = 'https://unpkg.com/leaflet@1.7.1/dist/leaflet.js';
    leafletScript.onload = function onLeafletLoaded() {
        let mapDiv = document.getElementById('testMap');
        if (!mapDiv) {
            mapDiv = document.createElement('div');
            mapDiv.id = 'testMap';
            mapDiv.style.height = '400px';
            mapDiv.style.width = '100%';
            document.body.appendChild(mapDiv);
        }

        const map = L.map('testMap').setView([51.505, -0.09], 13);
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            maxZoom: 19,
        }).addTo(map);
    };
    document.head.appendChild(leafletScript);
})();