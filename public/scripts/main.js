document.addEventListener('DOMContentLoaded', () => {
    
    // Data for grocery categories (Used in both the Bar Chart and the Order List)
    const categoryData = [
        { item: 'Apples', amount: 450, color: '#ef4444' },   
        { item: 'Carrots', amount: 290, color: '#f97316' },  
        { item: 'Bananas', amount: 380, color: '#eab308' },   
        { item: 'Spinach', amount: 150, color: '#22c55e' },   
        { item: 'Blueberries', amount: 210, color: '#0062ff' },  
    ];

    // 1. Populate the "What to Order" list dynamically to match the data above
    const orderList = document.getElementById('order-list');
    categoryData.forEach(data => {
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

    // 2. Initialize Bar Chart (Sales per Category)
    const ctxCategory = document.getElementById('categoryChart').getContext('2d');
    new Chart(ctxCategory, {
        type: 'bar',
        data: {
            labels: categoryData.map(d => d.item),
            datasets: [{
                label: 'Units Sold',
                data: categoryData.map(d => d.amount),
                backgroundColor: categoryData.map(d => d.color),
                borderRadius: 4,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false }
            },
            scales: {
                y: { beginAtZero: true }
            }
        }
    });

    // 3. Initialize Line Chart (Projected Sales in Days of the Week)
    const ctxProjected = document.getElementById('projectedChart').getContext('2d');
    new Chart(ctxProjected, {
        type: 'line',
        data: {
            labels: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'],
            datasets: [{
                label: 'Projected Sales ($)',
                data: [4100, 4300, 3900, 4600, 5200, 6800, 7100],
                borderColor: '#3b82f6', // Blue
                backgroundColor: 'rgba(59, 130, 246, 0.1)',
                borderWidth: 3,
                tension: 0.4, // Makes the line curved/smooth
                fill: true
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false }
            },
            scales: {
                y: { beginAtZero: false }
            }
        }
    });

// 4. Generate Order Report (CSV Export)
    const generateBtn = document.getElementById('generate-report-btn');
    
    generateBtn.addEventListener('click', () => {
        // Industry standard PO format fields
        const headers = ['PO Number', 'Issue Date', 'SKU', 'Item Description', 'Order Quantity', 'Est. Unit Price', 'Total Cost'];
        const date = new Date().toISOString().split('T')[0];
        const poNumber = `PO-${date.replace(/-/g, '')}-${Math.floor(Math.random() * 1000)}`;
        
        // Mock pricing matching your categoryData items
        const priceMap = { 
            'Apples': 0.50, 
            'Carrots': 0.75, 
            'Bananas': 0.25, 
            'Spinach': 1.20, 
            'Blueberries': 2.50 
        };
        
        let csvContent = headers.join(',') + '\n';
        
        // Generate rows based on the existing categoryData array
        categoryData.forEach((data, index) => {
            const sku = `SKU-100${index + 1}`;
            // Use priceMap value, or fallback to $1.00 if the item is missing
            const unitPrice = priceMap[data.item] !== undefined ? priceMap[data.item] : 1.00;
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

        // Create a downloadable Blob
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
});