document.addEventListener("DOMContentLoaded", () => {
  const search = document.getElementById("complaintSearch");
  if (search) {
    search.addEventListener("input", () => {
      const query = search.value.toLowerCase();
      document.querySelectorAll("#complaintsTable tbody tr").forEach(row => {
        row.style.display = row.innerText.toLowerCase().includes(query) ? "" : "none";
      });
    });
  }

  const photoInput = document.getElementById("photo");
  const preview = document.getElementById("photoPreview");
  if (photoInput && preview) {
    photoInput.addEventListener("change", () => {
      const file = photoInput.files && photoInput.files[0];
      if (!file) { preview.classList.add("hidden"); preview.removeAttribute("src"); return; }
      if (file.size > 5 * 1024 * 1024) {
        alert("Please select an image smaller than 5 MB.");
        photoInput.value = "";
        preview.classList.add("hidden");
        return;
      }
      preview.src = URL.createObjectURL(file);
      preview.classList.remove("hidden");
    });
  }

  document.querySelectorAll("[data-copy]").forEach(button => {
    button.addEventListener("click", async () => {
      const value = button.getAttribute("data-copy");
      try {
        await navigator.clipboard.writeText(value);
        button.textContent = "Copied!";
        setTimeout(() => button.textContent = "Copy", 1400);
      } catch (_) {
        window.prompt("Copy this reference ID:", value);
      }
    });
  });

  const pickMap = document.getElementById("pickMap");
  if (pickMap && window.L) {
    const latField = document.getElementById("latitude");
    const lonField = document.getElementById("longitude");
    const map = L.map(pickMap).setView([12.9141, 74.8560], 13);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: '&copy; OpenStreetMap contributors', maxZoom: 19
    }).addTo(map);
    let marker = null;
    function setPoint(lat, lon) {
      latField.value = Number(lat).toFixed(6);
      lonField.value = Number(lon).toFixed(6);
      if (marker) marker.setLatLng([lat, lon]);
      else marker = L.marker([lat, lon]).addTo(map);
    }
    map.on("click", e => setPoint(e.latlng.lat, e.latlng.lng));
    if (latField.value && lonField.value) setPoint(latField.value, lonField.value);
  }

  const hotspotMap = document.getElementById("hotspotMap");
  if (hotspotMap && window.L) {
    const map = L.map(hotspotMap).setView([12.9141, 74.8560], 13);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: '&copy; OpenStreetMap contributors', maxZoom: 19
    }).addTo(map);
    const data = window.CIVIC_HOTSPOT_DATA || [];
    data.forEach(h => {
      L.circleMarker([h.latitude, h.longitude], {
        radius: Math.min(22, 7 + h.count * 2),
        color: h.open ? "#e17b32" : "#139b8a",
        fillColor: h.open ? "#f5a45e" : "#139b8a",
        fillOpacity: 0.55,
        weight: 2
      }).addTo(map).bindPopup(`<b>${h.name}</b><br>${h.count} reports<br>${h.open} open<br>${h.categories}`);
    });
    if (data.length) map.fitBounds(data.map(h => [h.latitude, h.longitude]), {padding:[30,30], maxZoom: 15});
  }
});
