document.addEventListener('DOMContentLoaded', () => {
    // 1. Define Default & Updated Datasets for the Demo
    const defaultData = {
        customers: '1,248',
        sales: '$42.5k',
        waste: '340 lbs',
        items: '8,932',
        categories: [
            { item: 'Apples', amount: 450, color: '#ef4444', price: 0.50 },   
            { item: 'Carrots', amount: 290, color: '#f97316', price: 0.75 },  
            { item: 'Bananas', amount: 380, color: '#eab308', price: 0.25 },   
            { item: 'Spinach', amount: 150, color: '#22c55e', price: 1.20 },   
            { item: 'Blueberries', amount: 210, color: '#2563eb', price: 2.50 }
        ],
        projectedSales: [4100, 4300, 3900, 4600, 5200, 6800, 7100]
    };

    const uploadedData = {
        customers: '1,890',
        sales: '$58.2k',
        waste: '512 lbs',
        items: '12,410',
        categories: [
            { item: 'Avocados', amount: 620, color: '#15803d', price: 1.50 },   
            { item: 'Strawberries', amount: 510, color: '#dc2626', price: 3.00 },  
            { item: 'Almond Milk', amount: 390, color: '#d97706', price: 2.80 },   
            { item: 'Artisan Bread', amount: 310, color: '#854d0e', price: 3.50 },   
            { item: 'Fresh Eggs', amount: 440, color: '#f59e0b', price: 2.20 }
        ],
        projectedSales: [5200, 5600, 5100, 6100, 7400, 8900, 9300]
    };

    // 2. Check URL for '?uploaded=true'
    const urlParams = new URLSearchParams(window.location.search);
    const hasUploaded = urlParams.get('uploaded') === 'true';

    // Select active dataset
    const activeData = hasUploaded ? uploadedData : defaultData;

    // 3. Display Banner & Update Metric Cards
    if (hasUploaded) {
        const banner = document.getElementById('upload-success-banner');
        if (banner) banner.classList.remove('hidden');
    }

    document.getElementById('metric-customers').textContent = activeData.customers;
    document.getElementById('metric-sales').textContent = activeData.sales;
    document.getElementById('metric-waste').textContent = activeData.waste;
    document.getElementById('metric-items').textContent = activeData.items;

    // 4. Populate "What to Order" List
    const orderList = document.getElementById('order-list');
    orderList.innerHTML = ''; // Clear previous items
    
    activeData.categories.forEach(data => {
        const li = document.createElement('li');
        li.className = "flex justify-between items-center p-3 hover:bg-gray-50 rounded-lg border border-gray-100 transition";
        li.innerHTML = `
            <div class="flex items-center space-x-3">
                <span class="w-3 h-3 rounded-full" style="background-color: ${data.color}"></span>
                <span class="font-semibold text-gray-700">${data.item}</span>
            </div>
            <span class="font-bold text-gray-900">${data.amount} units</span>
        `;
        orderList.appendChild(li);
    });

    // 5. Initialize Bar Chart (Sales per Category)
    const ctxCategory = document.getElementById('categoryChart').getContext('2d');
    new Chart(ctxCategory, {
        type: 'bar',
        data: {
            labels: activeData.categories.map(d => d.item),
            datasets: [{
                label: 'Units Sold',
                data: activeData.categories.map(d => d.amount),
                backgroundColor: activeData.categories.map(d => d.color),
                borderRadius: 4,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: { y: { beginAtZero: true } }
        }
    });

    // 6. Initialize Line Chart (Projected Sales)
    const ctxProjected = document.getElementById('projectedChart').getContext('2d');
    new Chart(ctxProjected, {
        type: 'line',
        data: {
            labels: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'],
            datasets: [{
                label: 'Projected Sales ($)',
                data: activeData.projectedSales,
                borderColor: '#3b82f6',
                backgroundColor: 'rgba(59, 130, 246, 0.1)',
                borderWidth: 3,
                tension: 0.4,
                fill: true
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: { y: { beginAtZero: false } }
        }
    });

    // 7. Generate Order Report (CSV Export)
    const generateBtn = document.getElementById('generate-report-btn');
    if (generateBtn) {
        generateBtn.addEventListener('click', () => {
            const headers = ['PO Number', 'Issue Date', 'SKU', 'Item Description', 'Order Quantity', 'Est. Unit Price', 'Total Cost'];
            const date = new Date().toISOString().split('T')[0];
            const poNumber = `PO-${date.replace(/-/g, '')}-${Math.floor(Math.random() * 1000)}`;
            
            let csvContent = headers.join(',') + '\n';
            
            activeData.categories.forEach((data, index) => {
                const sku = `SKU-100${index + 1}`;
                const unitPrice = data.price;
                const totalCost = (data.amount * unitPrice).toFixed(2);
                
                const row = [
                    poNumber, 
                    date, 
                    sku, 
                    data.item, 
                    data.amount, 
                    `$${unitPrice.toFixed(2)}`, 
                    `$${totalCost}`
                ];
                csvContent += row.join(',') + '\n';
            });

            const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
            const link = document.createElement('a');
            const url = URL.createObjectURL(blob);
            
            link.setAttribute('href', url);
            link.setAttribute('download', `${poNumber}_Order_Report.csv`);
            link.style.visibility = 'hidden';
            
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
        });
    }
});