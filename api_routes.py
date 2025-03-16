from flask import Blueprint, jsonify
from datetime import datetime
import pytz
from api_endpoints import register_api_routes # Assuming this function exists and handles other routes


api_bp = Blueprint('api', __name__)

#Simplified API endpoints registration
register_api_routes(api_bp)

# Added route for dashboard data with standardized response format
@api_bp.route('/api/dashboard')
def get_dashboard_data():
    try:
        data = get_aggregated_data()
        if not data or 'dates' not in data or 'values' not in data:
            return jsonify({
                "status": "error",
                "message": "Invalid data structure"
            }), 400

        return jsonify({
            "status": "success",
            "data": {
                "timestamps": [d.isoformat() for d in data['dates']],
                "values": data['values'],
                "metadata": {
                    "last_updated": datetime.now(pytz.UTC).isoformat()
                }
            }
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


#Example of how to use this blueprint in a Flask app:
#from flask import Flask
#app = Flask(__name__)
#app.register_blueprint(api_bp, url_prefix='/api')
#if __name__ == '__main__':
#    app.run(debug=True)