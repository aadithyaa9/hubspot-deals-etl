"""
api/routes.py
--------------
Flask-RESTX route definitions.
Contains the original scan/pipeline/maintenance endpoints
PLUS the new HubSpot deals endpoints.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from flask_restx import Api, Resource, Namespace
from flask import request, g
from marshmallow import ValidationError
import logging
from datetime import datetime
import uuid

from .swagger_schemas import register_models
from .schemas import (
    validate_scan_request,
    validate_pagination_params,
    validate_cleanup_request,
    ScanConfig
)
from services.extraction_service import ExtractionService
from config import get_config
from loki_logger import get_logger, log_business_event, log_security_event

# Initialize logging
logger = get_logger(__name__)
executor = ThreadPoolExecutor(max_workers=4)


def create_api():
    """Create and configure the Flask-RESTX API"""
    config = get_config()
    api_config = config.get_api_config()

    api = Api(
        title=config.APP_TITLE,
        version=config.APP_VERSION,
        description=config.APP_DESCRIPTION,
        doc=api_config['docs_path'],
        prefix=api_config['prefix']
    )

    models = register_models(api)
    extraction_service = ExtractionService(config.get_extraction_config())

    # ── Original namespaces ───────────────────────────────────────────────────
    scan_ns = Namespace('scan', description='Scan operations')
    users_ns = Namespace('users', description='User-related operations')
    results_ns = Namespace('results', description='Results retrieval operations')
    pipeline_ns = Namespace('pipeline', description='Pipeline operations')
    maintenance_ns = Namespace('maintenance', description='Maintenance operations')

    api.add_namespace(scan_ns)
    api.add_namespace(users_ns)
    api.add_namespace(results_ns)
    api.add_namespace(pipeline_ns)
    api.add_namespace(maintenance_ns)

    # ── Scan endpoints ────────────────────────────────────────────────────────

    @scan_ns.route('/start')
    class StartScan(Resource):
        @scan_ns.expect(models['scan_request_model'])
        @scan_ns.response(400, 'Invalid request data')
        @scan_ns.response(409, 'Scan already exists')
        @scan_ns.response(500, 'Internal server error')
        def post(self):
            """Start a new data extraction scan"""
            request_id = getattr(g, 'request_id', str(uuid.uuid4()))
            try:
                json_data = request.get_json()
                if not json_data:
                    return {
                        "success": False,
                        "message": "No JSON data provided",
                        "error": "No JSON data provided"
                    }, 400

                try:
                    validated_config = validate_scan_request(json_data)
                except ValidationError as err:
                    return {
                        "success": False,
                        "message": f"Configuration validation failed: {err.messages}",
                        "error": f"Configuration validation failed: {err.messages}",
                        "validation_errors": err.messages
                    }, 400

                scan_config = ScanConfig(
                    scanId=validated_config['scanId'],
                    organizationId=validated_config['organizationId'],
                    type=validated_config['type'],
                    auth=validated_config['auth'],
                    filters=validated_config['filters']
                )

                existing_scan = extraction_service.get_scan_status(scan_config.scanId)
                if existing_scan:
                    return {
                        "success": False,
                        "message": f"A scan with ID '{scan_config.scanId}' already exists",
                        "error": f"A scan with ID '{scan_config.scanId}' already exists"
                    }, 409

                executor.submit(asyncio.run, extraction_service.start_scan(validated_config))

                log_business_event(
                    logger, "scan_creation_accepted",
                    scan_id=scan_config.scanId,
                    organization_id=scan_config.organizationId
                )
                return {
                    "success": True,
                    "message": "Scan initialization accepted and is now processing in the background."
                }, 202

            except Exception as e:
                logger.error("Error in start_scan", extra={
                    'request_id': request_id, 'error': str(e)
                }, exc_info=True)
                return {
                    "success": False,
                    "message": f"An unexpected error occurred: {str(e)}",
                    "error": str(e)
                }, 500

    @scan_ns.route('/<string:scan_id>/status')
    class ScanStatus(Resource):
        @scan_ns.response(404, 'Scan not found')
        @scan_ns.response(500, 'Internal server error')
        def get(self, scan_id):
            """Get the status of a specific scan"""
            request_id = getattr(g, 'request_id', str(uuid.uuid4()))
            try:
                scan_status = extraction_service.get_scan_status(scan_id)
                if not scan_status:
                    return {
                        "success": False,
                        "message": f"No scan found with ID: {scan_id}",
                        "error": f"No scan found with ID: {scan_id}"
                    }, 404
                return {"success": True, "data": scan_status}
            except Exception as e:
                logger.error("Error getting scan status", extra={
                    'request_id': request_id, 'scan_id': scan_id, 'error': str(e)
                }, exc_info=True)
                return {
                    "success": False,
                    "message": f"Failed to retrieve scan status: {str(e)}",
                    "error": str(e)
                }, 500

    @scan_ns.route('/<string:scan_id>/cancel')
    class CancelScan(Resource):
        @scan_ns.response(400, 'Cannot cancel scan')
        @scan_ns.response(404, 'Scan not found')
        @scan_ns.response(500, 'Internal server error')
        def post(self, scan_id):
            """Cancel a running scan"""
            request_id = getattr(g, 'request_id', str(uuid.uuid4()))
            try:
                result = extraction_service.cancel_scan(scan_id)
                if result['success']:
                    log_business_event(logger, "scan_cancelled", scan_id=scan_id)
                    return result
                else:
                    return {
                        "success": False,
                        "message": result['message'],
                        "error": result['message']
                    }, 400
            except Exception as e:
                logger.error("Error cancelling scan", extra={
                    'request_id': request_id, 'scan_id': scan_id, 'error': str(e)
                }, exc_info=True)
                return {
                    "success": False,
                    "message": f"Failed to cancel scan: {str(e)}",
                    "error": str(e)
                }, 500

    @scan_ns.route('/list')
    class ListScans(Resource):
        @scan_ns.param('organizationId', 'Filter by organization ID')
        @scan_ns.param('limit', 'Number of results per page', type=int,
                       default=api_config['default_scan_list_limit'])
        @scan_ns.param('offset', 'Number of results to skip', type=int, default=0)
        def get(self):
            """List all scans with optional filtering and pagination"""
            request_id = getattr(g, 'request_id', str(uuid.uuid4()))
            try:
                organization_id = request.args.get('organizationId')
                try:
                    limit, offset = validate_pagination_params(
                        request.args.get('limit', api_config['default_scan_list_limit']),
                        request.args.get('offset', 0),
                        max_limit=api_config['max_scan_list_limit']
                    )
                except ValidationError as err:
                    return {
                        "success": False,
                        "message": f"Validation error: {err.messages}",
                        "error": f"Validation error: {err.messages}",
                        "validation_errors": err.messages
                    }, 400

                scans = extraction_service.list_scans(organization_id, limit, offset)
                total = len(scans) + offset if len(scans) == limit else offset + len(scans)
                return {
                    "success": True,
                    "data": {
                        "scans": scans,
                        "pagination": {
                            "total": total,
                            "limit": limit,
                            "offset": offset,
                            "hasMore": len(scans) == limit,
                            "returned": len(scans)
                        }
                    }
                }
            except Exception as e:
                logger.error("Error listing scans", extra={
                    'request_id': request_id, 'error': str(e)
                }, exc_info=True)
                return {
                    "success": False,
                    "message": f"Failed to list scans: {str(e)}",
                    "error": str(e)
                }, 500

    @scan_ns.route('/statistics')
    class ScanStatistics(Resource):
        @scan_ns.param('organizationId', 'Filter statistics by organization ID')
        def get(self):
            """Get scan statistics"""
            request_id = getattr(g, 'request_id', str(uuid.uuid4()))
            try:
                organization_id = request.args.get('organizationId')
                statistics = extraction_service.get_scan_statistics(organization_id)
                return {"success": True, "data": statistics}
            except Exception as e:
                logger.error("Error getting statistics", extra={
                    'request_id': request_id, 'error': str(e)
                }, exc_info=True)
                return {
                    "success": False,
                    "message": f"Failed to retrieve scan statistics: {str(e)}",
                    "error": str(e)
                }, 500

    # ── Results endpoints ─────────────────────────────────────────────────────

    @results_ns.route('/<string:scan_id>/tables')
    class GetAvailableTables(Resource):
        @results_ns.response(404, 'Scan not found')
        @results_ns.response(400, 'Scan not completed')
        @results_ns.response(500, 'Internal server error')
        def get(self, scan_id):
            """Get available tables for a completed scan"""
            request_id = getattr(g, 'request_id', str(uuid.uuid4()))
            try:
                result = extraction_service.get_available_tables(scan_id)
                if result['success']:
                    return {"success": True, "data": result['data']}
                else:
                    status_code = 404 if "not found" in result['message'].lower() else 400
                    return {
                        "success": False,
                        "message": result['message'],
                        "error": result['message']
                    }, status_code
            except Exception as e:
                logger.error("Error getting available tables", extra={
                    'request_id': request_id, 'scan_id': scan_id, 'error': str(e)
                }, exc_info=True)
                return {
                    "success": False,
                    "message": f"Failed to retrieve available tables: {str(e)}",
                    "error": str(e)
                }, 500

    @results_ns.route('/<string:scan_id>/result')
    class GetScanResults(Resource):
        @results_ns.param('tableName', 'Name of the table to query', default='users')
        @results_ns.param('limit', 'Number of records per page', type=int,
                          default=api_config['default_results_limit'])
        @results_ns.param('offset', 'Number of records to skip', type=int, default=0)
        @results_ns.response(404, 'Scan not found')
        @results_ns.response(400, 'Scan not completed or invalid parameters')
        @results_ns.response(500, 'Internal server error')
        def get(self, scan_id):
            """Get scan results with pagination and table selection"""
            request_id = getattr(g, 'request_id', str(uuid.uuid4()))
            try:
                table_name = request.args.get('tableName', 'users')
                try:
                    limit, offset = validate_pagination_params(
                        request.args.get('limit', api_config['default_results_limit']),
                        request.args.get('offset', 0),
                        max_limit=api_config['max_results_limit']
                    )
                except ValidationError as err:
                    return {
                        "success": False,
                        "message": f"Validation error: {err.messages}",
                        "error": f"Validation error: {err.messages}",
                        "validation_errors": err.messages
                    }, 400

                result = extraction_service.get_scan_results(scan_id, table_name, limit, offset)
                if result['success']:
                    return {"success": True, "data": result['data']}
                else:
                    status_code = 404 if "not found" in result['message'].lower() else 400
                    return {
                        "success": False,
                        "message": result['message'],
                        "error": result['message']
                    }, status_code
            except Exception as e:
                logger.error("Error getting scan results", extra={
                    'request_id': request_id, 'scan_id': scan_id, 'error': str(e)
                }, exc_info=True)
                return {
                    "success": False,
                    "message": f"Failed to retrieve scan results: {str(e)}",
                    "error": str(e)
                }, 500

    # ── Pipeline endpoints ────────────────────────────────────────────────────

    @pipeline_ns.route('/info')
    class PipelineInfo(Resource):
        def get(self):
            """Get information about the DLT pipeline configuration"""
            request_id = getattr(g, 'request_id', str(uuid.uuid4()))
            try:
                pipeline_info = extraction_service.get_pipeline_info()
                return {"success": True, "data": pipeline_info}
            except Exception as e:
                logger.error("Error getting pipeline info", extra={
                    'request_id': request_id, 'error': str(e)
                }, exc_info=True)
                return {
                    "success": False,
                    "message": f"Failed to retrieve pipeline information: {str(e)}",
                    "error": str(e)
                }, 500

    # ── Maintenance endpoints ─────────────────────────────────────────────────

    @maintenance_ns.route('/cleanup')
    class Cleanup(Resource):
        @maintenance_ns.expect(models['cleanup_request_model'])
        @maintenance_ns.response(400, 'Invalid request data')
        def post(self):
            """Clean up old scan results"""
            request_id = getattr(g, 'request_id', str(uuid.uuid4()))
            try:
                json_data = request.get_json() or {}
                try:
                    days_old = validate_cleanup_request(json_data)
                except ValidationError as err:
                    return {
                        "success": False,
                        "message": f"Validation error: {err.messages}",
                        "error": f"Validation error: {err.messages}",
                        "validation_errors": err.messages
                    }, 400

                cleaned_count = extraction_service.cleanup_old_scans(days_old)
                log_security_event(
                    logger, "data_cleanup_performed",
                    cleaned_count=cleaned_count,
                    days_old=days_old
                )
                return {
                    "success": True,
                    "data": {"cleanedCount": cleaned_count, "daysOld": days_old},
                    "message": f"Successfully cleaned up {cleaned_count} old scan results"
                }
            except Exception as e:
                logger.error("Error during cleanup", extra={
                    'request_id': request_id, 'error': str(e)
                }, exc_info=True)
                return {
                    "success": False,
                    "message": f"Failed to clean up old scans: {str(e)}",
                    "error": str(e)
                }, 500

    @maintenance_ns.route('/detect-crashed')
    class DetectCrashedJobs(Resource):
        @maintenance_ns.param(
            'timeoutMinutes',
            'Timeout in minutes for crash detection',
            type=int,
            default=api_config['crash_detection_timeout']
        )
        @maintenance_ns.marshal_with(models['api_response_model'])
        def post(self):
            """Detect and mark crashed jobs"""
            request_id = getattr(g, 'request_id', str(uuid.uuid4()))
            try:
                timeout_minutes = request.args.get(
                    'timeoutMinutes',
                    api_config['crash_detection_timeout'],
                    type=int
                )
                if timeout_minutes < 1 or timeout_minutes > api_config['max_crash_detection_timeout']:
                    return {
                        "success": False,
                        "message": f"Timeout must be between 1 and {api_config['max_crash_detection_timeout']}",
                        "error": "Invalid timeout value"
                    }, 400

                crashed_job_ids = extraction_service.detect_crashed_jobs(timeout_minutes)
                if crashed_job_ids:
                    log_security_event(
                        logger, "crashed_jobs_detected",
                        severity='WARNING',
                        crashed_count=len(crashed_job_ids)
                    )
                return {
                    "success": True,
                    "data": {
                        "crashedJobIds": crashed_job_ids,
                        "crashedCount": len(crashed_job_ids),
                        "timeoutMinutes": timeout_minutes
                    },
                    "message": f"Detected {len(crashed_job_ids)} crashed jobs"
                }
            except Exception as e:
                logger.error("Error detecting crashed jobs", extra={
                    'request_id': request_id, 'error': str(e)
                }, exc_info=True)
                return {
                    "success": False,
                    "message": f"Failed to detect crashed jobs: {str(e)}",
                    "error": str(e)
                }, 500

    @scan_ns.route('/<string:scan_id>/remove')
    class RemoveScan(Resource):
        @scan_ns.response(404, 'Scan not found')
        @scan_ns.response(400, 'Cannot remove active scan')
        @scan_ns.response(500, 'Internal server error')
        def delete(self, scan_id):
            """Remove a scan and its data"""
            request_id = getattr(g, 'request_id', str(uuid.uuid4()))
            try:
                scan_status = extraction_service.get_scan_status(scan_id)
                if not scan_status:
                    return {
                        "success": False,
                        "message": f"No scan found with ID: {scan_id}",
                        "error": f"No scan found with ID: {scan_id}"
                    }, 404

                if scan_status['status'] in ['running', 'pending']:
                    return {
                        "success": False,
                        "message": "Cannot remove active scan. Please cancel first.",
                        "error": "Cannot remove active scan"
                    }, 400

                result = extraction_service.remove_scan(scan_id)
                if result['success']:
                    log_business_event(logger, "scan_removed", scan_id=scan_id)
                    return result
                else:
                    return {
                        "success": False,
                        "message": result['message'],
                        "error": result['message']
                    }, 400
            except Exception as e:
                logger.error("Error removing scan", extra={
                    'request_id': request_id, 'scan_id': scan_id, 'error': str(e)
                }, exc_info=True)
                return {
                    "success": False,
                    "message": f"Failed to remove scan: {str(e)}",
                    "error": str(e)
                }, 500

    @scan_ns.route('/<string:scan_id>/pause')
    class PauseScan(Resource):
        @scan_ns.response(400, 'Cannot pause scan')
        @scan_ns.response(404, 'Scan not found')
        @scan_ns.response(500, 'Internal server error')
        def post(self, scan_id):
            """Pause a running scan"""
            request_id = getattr(g, 'request_id', str(uuid.uuid4()))
            try:
                result = extraction_service.pause_scan(scan_id)
                if result['success']:
                    return result
                else:
                    status_code = 404 if "not found" in result['message'].lower() else 400
                    return {
                        "success": False,
                        "message": result['message'],
                        "error": result['message']
                    }, status_code
            except Exception as e:
                logger.error("Error pausing scan", extra={
                    'request_id': request_id, 'scan_id': scan_id, 'error': str(e)
                }, exc_info=True)
                return {
                    "success": False,
                    "message": f"Failed to pause scan: {str(e)}",
                    "error": str(e)
                }, 500

    @api.route('/stats')
    class ServiceStats(Resource):
        def get(self):
            """Get service statistics"""
            try:
                stats = extraction_service.get_service_statistics()
                return {"success": True, "data": stats}
            except Exception as e:
                logger.error("Error getting service statistics", extra={'error': str(e)})
                return {
                    "success": False,
                    "message": f"Failed to retrieve service statistics: {str(e)}",
                    "error": str(e)
                }, 500

    @api.route('/health')
    class Health(Resource):
        def get(self):
            """Health check endpoint"""
            try:
                pipeline_info = extraction_service.get_pipeline_info()
                return {
                    "status": "healthy",
                    "timestamp": datetime.utcnow().isoformat(),
                    "service": config.DLT_PIPELINE_NAME,
                    "pipeline": pipeline_info
                }
            except Exception as e:
                logger.error("Health check failed", extra={'error': str(e)})
                return {
                    "status": "unhealthy",
                    "timestamp": datetime.utcnow().isoformat(),
                    "service": config.DLT_PIPELINE_NAME,
                    "error": str(e)
                }, 500

    logger.info("API created successfully", extra={
        'operation': 'api_creation',
        'service': config.DLT_PIPELINE_NAME
    })
    return api


# ── HubSpot Deals namespace ───────────────────────────────────────────────────

def add_deals_namespace(api):
    """Add HubSpot deals endpoints to the existing Flask-RESTX API."""
    from models.database import get_db_manager
    from models.models import Deal
    from sqlalchemy import func

    deals_ns = Namespace('deals', description='HubSpot extracted deals')
    api.add_namespace(deals_ns)

    @deals_ns.route('/')
    class DealList(Resource):
        @deals_ns.param('deal_stage', 'Filter by exact stage e.g. closedwon')
        @deals_ns.param('pipeline', 'Filter by pipeline identifier')
        @deals_ns.param('amount_min', 'Minimum deal amount', type=float)
        @deals_ns.param('amount_max', 'Maximum deal amount', type=float)
        @deals_ns.param('close_date_after', 'close_date >= YYYY-MM-DD')
        @deals_ns.param('close_date_before', 'close_date <= YYYY-MM-DD')
        @deals_ns.param('limit', 'Results per page max 100', type=int, default=25)
        @deals_ns.param('offset', 'Results to skip', type=int, default=0)
        def get(self):
            """List all extracted HubSpot deals with filtering and pagination."""
            try:
                db = get_db_manager()
                with db.session_scope() as session:
                    query = session.query(Deal)

                    stage = request.args.get('deal_stage')
                    pipeline = request.args.get('pipeline')
                    amount_min = request.args.get('amount_min', type=float)
                    amount_max = request.args.get('amount_max', type=float)
                    close_after = request.args.get('close_date_after')
                    close_before = request.args.get('close_date_before')

                    if stage:
                        query = query.filter(Deal.deal_stage.ilike(stage))
                    if pipeline:
                        query = query.filter(Deal.pipeline.ilike(pipeline))
                    if amount_min is not None:
                        query = query.filter(Deal.amount >= amount_min)
                    if amount_max is not None:
                        query = query.filter(Deal.amount <= amount_max)
                    if close_after:
                        query = query.filter(Deal.close_date >= close_after)
                    if close_before:
                        query = query.filter(Deal.close_date <= close_before)

                    total = query.count()
                    limit = min(request.args.get('limit', 25, type=int), 100)
                    offset = request.args.get('offset', 0, type=int)

                    deals = (
                        query
                        .order_by(Deal.close_date.desc())
                        .limit(limit)
                        .offset(offset)
                        .all()
                    )

                    return {
                        "success": True,
                        "data": {
                            "deals": [d.to_dict() for d in deals],
                            "pagination": {
                                "total": total,
                                "limit": limit,
                                "offset": offset,
                                "returned": len(deals),
                                "hasMore": (offset + len(deals)) < total
                            }
                        }
                    }
            except Exception as e:
                logger.error("Error listing deals: %s", str(e), exc_info=True)
                return {
                    "success": False,
                    "message": f"Failed to retrieve deals: {str(e)}",
                    "error": str(e)
                }, 500

    @deals_ns.route('/summary')
    class DealSummary(Resource):
        def get(self):
            """Aggregated statistics across all extracted deals."""
            try:
                db = get_db_manager()
                with db.session_scope() as session:
                    total = session.query(
                        func.count(Deal.deal_id)
                    ).scalar()

                    total_amount = session.query(
                        func.sum(Deal.amount)
                    ).scalar()

                    avg_amount = session.query(
                        func.avg(Deal.amount)
                    ).scalar()

                    last_extracted = session.query(
                        func.max(Deal.extracted_at)
                    ).scalar()

                    stage_rows = (
                        session.query(
                            Deal.deal_stage,
                            func.count(Deal.deal_id)
                        )
                        .group_by(Deal.deal_stage)
                        .order_by(func.count(Deal.deal_id).desc())
                        .all()
                    )

                    stages = {
                        (row[0] or "unknown"): row[1]
                        for row in stage_rows
                    }

                    return {
                        "success": True,
                        "data": {
                            "total_deals": total,
                            "total_amount": float(total_amount) if total_amount else None,
                            "average_amount": float(avg_amount) if avg_amount else None,
                            "stages": stages,
                            "last_extracted_at": (
                                last_extracted.isoformat()
                                if last_extracted else None
                            )
                        }
                    }
            except Exception as e:
                logger.error("Error getting deal summary: %s", str(e), exc_info=True)
                return {
                    "success": False,
                    "message": f"Failed to retrieve summary: {str(e)}",
                    "error": str(e)
                }, 500

    @deals_ns.route('/<string:deal_id>')
    class DealDetail(Resource):
        @deals_ns.response(404, 'Deal not found')
        @deals_ns.response(500, 'Internal server error')
        def get(self, deal_id):
            """Get a single deal by its HubSpot deal_id."""
            try:
                db = get_db_manager()
                with db.session_scope() as session:
                    deal = session.query(Deal).filter(
                        Deal.deal_id == deal_id
                    ).first()

                    if not deal:
                        return {
                            "success": False,
                            "message": f"No deal found with id: {deal_id}",
                            "error": "Not found"
                        }, 404

                    return {"success": True, "data": deal.to_dict()}
            except Exception as e:
                logger.error("Error fetching deal %s: %s", deal_id, str(e), exc_info=True)
                return {
                    "success": False,
                    "message": f"Failed to retrieve deal: {str(e)}",
                    "error": str(e)
                }, 500

    return deals_ns